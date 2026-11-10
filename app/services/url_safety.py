"""URL validation and SSRF protection.

Every outbound request goes through these checks:

* only http/https with a hostname, default or explicit port 80/443/8080/8443;
* no credentials embedded in the URL;
* the hostname must not be localhost / an internal-looking name;
* every IP the hostname resolves to must be globally routable
  (private, loopback, link-local, multicast, reserved, CGNAT, etc. are
  rejected).

The HTTP client re-runs these checks for every redirect hop, and the
connection layer (`scrapers.fetcher.GuardedNetworkBackend`) re-resolves and
pins the validated IP when the socket is opened, which defeats DNS rebinding.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

ALLOWED_SCHEMES = ("http", "https")
ALLOWED_PORTS = {None, 80, 443, 8080, 8443}
BLOCKED_HOSTNAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
BLOCKED_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".intranet",
    ".lan",
    ".home",
    ".corp",
    ".localdomain",
    ".home.arpa",
)

# Networks that `ipaddress` does not flag but which must not be reachable.
_EXTRA_BLOCKED = [
    ipaddress.ip_network("100.64.0.0/10"),  # carrier-grade NAT
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("198.18.0.0/15"),  # benchmarking
]

_ALT_IPV4 = re.compile(r"(?:0x[0-9a-f]+|\d+)(?:\.(?:0x[0-9a-f]+|\d+)){0,3}", re.IGNORECASE)

Resolver = Callable[[str, int], Awaitable[list[str]]]


class UnsafeURLError(ValueError):
    """Raised when a URL is not allowed. The message is safe to show users."""


def normalize_website_url(raw: str) -> str:
    """Turn user input like 'amazon.com' into 'https://amazon.com/'.

    Raises UnsafeURLError for anything that is not a plausible http(s) URL.
    """
    value = (raw or "").strip()
    if not value:
        raise UnsafeURLError("Website URL is empty.")
    if len(value) > 2048:
        raise UnsafeURLError("Website URL is too long.")
    if "://" not in value:
        value = "https://" + value
    try:
        parts = urlsplit(value)
    except ValueError as exc:
        raise UnsafeURLError("Website URL is not valid.") from exc
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURLError("Only http:// and https:// websites are supported.")
    check_url_syntax(value)
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def check_url_syntax(url: str) -> str:
    """Validate everything that can be checked without DNS. Returns hostname."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise UnsafeURLError("URL is not valid.") from exc
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURLError("Only http:// and https:// URLs are allowed.")
    if parts.username or parts.password:
        raise UnsafeURLError("URLs containing credentials are not allowed.")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise UnsafeURLError("URL has no hostname.")
    if port not in ALLOWED_PORTS:
        raise UnsafeURLError("Only standard web ports are allowed.")
    if host in BLOCKED_HOSTNAMES or host.endswith(BLOCKED_SUFFIXES):
        raise UnsafeURLError("Local or internal hostnames are not allowed.")

    literal = _parse_ip_literal(host)
    if literal is not None:
        if not is_public_ip(literal):
            raise UnsafeURLError("Private or internal network addresses are not allowed.")
    elif "." not in host:
        # Single-label names (e.g. "router", "intranet") resolve internally.
        raise UnsafeURLError("Hostname must be a fully qualified domain name.")
    return host


def _parse_ip_literal(host: str) -> Optional[ipaddress._BaseAddress]:
    candidate = host.strip("[]")
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        pass
    # Alternative IPv4 notations (decimal, octal, hex, short forms) such as
    # http://2130706433/ or http://0x7f.1/ are interpreted as IPs by many
    # resolvers, so treat them as IP literals too.
    if _ALT_IPV4.fullmatch(candidate):
        try:
            return ipaddress.ip_address(socket.inet_aton(candidate))
        except OSError:
            return None
    return None


def is_public_ip(ip: ipaddress._BaseAddress | str) -> bool:
    if isinstance(ip, str):
        ip = ipaddress.ip_address(ip)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    ):
        return False
    return not any(ip in net for net in _EXTRA_BLOCKED if net.version == ip.version)


async def system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


async def resolve_public_addresses(
    host: str, port: int, resolver: Resolver = system_resolver
) -> list[str]:
    """Resolve `host` and ensure *every* address is public. Returns the IPs."""
    literal = _parse_ip_literal(host)
    if literal is not None:
        if not is_public_ip(literal):
            raise UnsafeURLError("Private or internal network addresses are not allowed.")
        return [str(literal)]
    try:
        addresses = await resolver(host, port)
    except (OSError, UnicodeError) as exc:
        raise UnsafeURLError("Website address could not be resolved.") from exc
    if not addresses:
        raise UnsafeURLError("Website address could not be resolved.")
    for address in addresses:
        try:
            if not is_public_ip(address.split("%", 1)[0]):
                raise UnsafeURLError(
                    "Website resolves to a private or internal network address."
                )
        except ValueError as exc:
            raise UnsafeURLError("Website address could not be resolved.") from exc
    return addresses


async def validate_url(
    url: str,
    resolver: Resolver = system_resolver,
    allow_private: bool = False,
) -> str:
    """Full validation (syntax + DNS). Returns the hostname.

    `allow_private` exists only for tests/local development and skips the
    network-destination checks (scheme checks still apply).
    """
    parts = urlsplit(url)
    if allow_private:
        if parts.scheme not in ALLOWED_SCHEMES or not parts.hostname:
            raise UnsafeURLError("Only http:// and https:// URLs are allowed.")
        return parts.hostname
    host = check_url_syntax(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    await resolve_public_addresses(host, port, resolver)
    return host
