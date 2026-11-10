"""Safe outbound HTTP client used by every adapter.

Responsibilities:
  * SSRF protection: URL/DNS validation before every request *and* every
    redirect hop (redirects are followed manually), plus a connection-level
    guard that resolves, validates and pins the IP actually connected to;
  * robots.txt compliance (RFC 9309 semantics, cached per origin);
  * timeouts, response-size limits and content-type allow-listing;
  * polite per-host rate limiting;
  * translating failures into user-safe error messages.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpcore
import httpx

from app.config import Settings
from app.services.url_safety import (
    Resolver,
    UnsafeURLError,
    resolve_public_addresses,
    system_resolver,
    validate_url,
)

logger = logging.getLogger(__name__)

ROBOTS_USER_AGENT = "PriceScout"
ROBOTS_MAX_BYTES = 512 * 1024
ALLOWED_CONTENT_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "application/json",
    "application/ld+json",
    "text/plain",
    "text/xml",
    "application/xml",
    "application/opensearchdescription+xml",
)


class FetchError(Exception):
    """A request failed. `user_message` is safe to display."""

    def __init__(self, user_message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.status_code = status_code


class BlockedError(FetchError):
    """The website refused automated access (403/429, CAPTCHA, bot wall)."""


class RobotsDisallowedError(FetchError):
    """robots.txt does not allow fetching this URL."""


@dataclass
class FetchResult:
    url: str
    status_code: int
    text: str
    content_type: str
    headers: dict[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        import json

        return json.loads(self.text)


class GuardedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Resolves the hostname itself, rejects non-public IPs, then connects to
    the validated IP. TLS still uses the original hostname for SNI and
    certificate verification (httpcore passes it to `start_tls`), so pinning
    the IP here defeats DNS-rebinding between validation and connection."""

    def __init__(self, resolver: Resolver, inner: Optional[httpcore.AsyncNetworkBackend] = None):
        self._resolver = resolver
        self._inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):  # noqa: ANN001
        try:
            addresses = await resolve_public_addresses(host, port, self._resolver)
        except UnsafeURLError as exc:
            raise httpcore.ConnectError(str(exc)) from exc
        last_error: Optional[Exception] = None
        for address in addresses:
            try:
                return await self._inner.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout, OSError) as exc:
                last_error = exc
        raise httpcore.ConnectError(str(last_error or "connection failed"))

    async def connect_unix_socket(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise httpcore.ConnectError("Unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def build_guarded_transport(resolver: Resolver) -> httpx.AsyncHTTPTransport:
    transport = httpx.AsyncHTTPTransport(retries=0, http2=False)
    pool = getattr(transport, "_pool", None)
    if pool is None or not hasattr(pool, "_network_backend"):  # pragma: no cover
        raise RuntimeError("Unsupported httpx/httpcore version for connection guarding")
    pool._network_backend = GuardedNetworkBackend(resolver, pool._network_backend)
    return transport


class SafeFetcher:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        resolver: Resolver = system_resolver,
    ) -> None:
        self._settings = settings
        self._resolver = resolver
        if transport is None:
            transport = (
                httpx.AsyncHTTPTransport(retries=0)
                if settings.allow_private_networks
                else build_guarded_transport(resolver)
            )
        self._client = httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,  # never route through env-configured proxies
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            headers={
                "User-Agent": settings.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._last_request_at: dict[str, float] = {}
        self._robots: dict[str, Optional[RobotFileParser]] = {}
        self._robots_locks: dict[str, asyncio.Lock] = {}

    async def __aenter__(self) -> "SafeFetcher":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- public API -----------------------------------------------------
    async def get(
        self,
        url: str,
        *,
        check_robots: bool = True,
        headers: Optional[dict[str, str]] = None,
        params: Optional[dict[str, str]] = None,
    ) -> FetchResult:
        if params:
            url = str(httpx.URL(url, params=params))
        if check_robots and self._settings.respect_robots_txt:
            await self.ensure_allowed_by_robots(url)
        return await self._request("GET", url, headers=headers)

    async def post(
        self,
        url: str,
        *,
        data: Optional[dict[str, str]] = None,
        headers: Optional[dict[str, str]] = None,
        auth: Optional[tuple[str, str]] = None,
    ) -> FetchResult:
        """POST without robots checks — only for official API endpoints."""
        return await self._request("POST", url, headers=headers, data=data, auth=auth)

    async def ensure_allowed_by_robots(self, url: str) -> None:
        parser = await self._robots_for(url)
        if parser is not None and not parser.can_fetch(ROBOTS_USER_AGENT, url):
            raise RobotsDisallowedError(
                "The website's robots.txt does not allow automated searches."
            )

    # -- internals ------------------------------------------------------
    async def _robots_for(self, url: str) -> Optional[RobotFileParser]:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        lock = self._robots_locks.setdefault(origin, asyncio.Lock())
        async with lock:
            if origin in self._robots:
                return self._robots[origin]
            parser: Optional[RobotFileParser] = RobotFileParser()
            try:
                result = await self._request(
                    "GET", origin + "/robots.txt", max_bytes=ROBOTS_MAX_BYTES, raise_for_status=False
                )
                if result.status_code >= 500:
                    # RFC 9309: unreachable robots.txt => assume full disallow.
                    parser.parse(["User-agent: *", "Disallow: /"])
                elif result.status_code >= 400:
                    parser = None  # unavailable => no restrictions
                else:
                    parser.parse(result.text.splitlines())
            except FetchError as exc:
                if isinstance(exc, BlockedError):
                    raise
                logger.info("robots.txt unreachable for %s: %s", origin, exc.user_message)
                parser.parse(["User-agent: *", "Disallow: /"])
            self._robots[origin] = parser
            return parser

    async def _throttle(self, host: str) -> None:
        interval = self._settings.per_host_min_interval_seconds
        if interval <= 0:
            return
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            last = self._last_request_at.get(host)
            now = time.monotonic()
            if last is not None and now - last < interval:
                await asyncio.sleep(interval - (now - last))
            self._last_request_at[host] = time.monotonic()

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        data: Optional[dict[str, str]] = None,
        auth: Optional[tuple[str, str]] = None,
        max_bytes: Optional[int] = None,
        raise_for_status: bool = True,
    ) -> FetchResult:
        max_bytes = max_bytes or self._settings.max_response_bytes
        current_url = url
        for _hop in range(self._settings.max_redirects + 1):
            try:
                host = await validate_url(
                    current_url,
                    resolver=self._resolver,
                    allow_private=self._settings.allow_private_networks,
                )
            except UnsafeURLError as exc:
                raise FetchError(str(exc)) from exc
            await self._throttle(host)
            try:
                async with self._client.stream(
                    method, current_url, headers=headers, data=data, auth=auth
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise FetchError("The website returned an invalid redirect.")
                        current_url = urljoin(current_url, location)
                        if response.status_code in (301, 302, 303) and method == "POST":
                            method, data = "GET", None
                        continue
                    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
                    if raise_for_status:
                        self._raise_for_status(response.status_code)
                    if content_type and not content_type.startswith(ALLOWED_CONTENT_TYPES):
                        raise FetchError("The website returned an unsupported content type.")
                    body = await self._read_limited(response, max_bytes)
                    encoding = response.charset_encoding or "utf-8"
                    try:
                        text = body.decode(encoding, errors="replace")
                    except LookupError:
                        text = body.decode("utf-8", errors="replace")
                    return FetchResult(
                        url=str(response.url),
                        status_code=response.status_code,
                        text=text,
                        content_type=content_type,
                        headers={k.lower(): v for k, v in response.headers.items()},
                    )
            except FetchError:
                raise
            except httpx.TimeoutException as exc:
                raise FetchError("The website took too long to respond.") from exc
            except httpx.ConnectError as exc:
                if "private or internal" in str(exc).lower():
                    raise FetchError("Website resolves to a private or internal network address.") from exc
                raise FetchError("Could not connect to the website.") from exc
            except httpx.HTTPError as exc:
                logger.info("HTTP error for %s: %r", current_url, exc)
                raise FetchError("The website could not be reached.") from exc
        raise FetchError("The website redirected too many times.")

    @staticmethod
    def _raise_for_status(status: int) -> None:
        if status < 400:
            return
        if status in (401, 403):
            raise BlockedError("Request blocked by the website (access denied).", status_code=status)
        if status == 429:
            raise BlockedError("Request blocked by the website (rate limited).", status_code=status)
        if status == 404:
            raise FetchError("The search page was not found on this website.", status_code=status)
        if status >= 500:
            raise FetchError("The website is currently unavailable.", status_code=status)
        raise FetchError(f"The website returned an error (HTTP {status}).", status_code=status)

    @staticmethod
    async def _read_limited(response: httpx.Response, max_bytes: int) -> bytes:
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise FetchError("The website's response was too large.")
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > max_bytes:
                raise FetchError("The website's response was too large.")
            chunks.append(chunk)
        return b"".join(chunks)
