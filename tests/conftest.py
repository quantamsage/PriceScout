"""Shared test fixtures. Tests never touch live websites."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}",
        playwright_enabled=False,
        per_host_min_interval_seconds=0,
        request_timeout_seconds=5,
        site_timeout_seconds=10,
        exchange_rates="",
        exchange_rate_provider_url=None,
        ebay_client_id=None,
        ebay_client_secret=None,
        _env_file=None,
    )


# ---------------------------------------------------------------------------
# HTTP mocking helpers
# ---------------------------------------------------------------------------

import httpx  # noqa: E402

PUBLIC_IP = "93.184.216.34"


def make_resolver(overrides: dict[str, list[str]] | None = None):
    """Fake DNS: every host resolves to a public IP unless overridden."""
    overrides = overrides or {}

    async def resolve(host: str, port: int) -> list[str]:
        if host in overrides:
            return overrides[host]
        return [PUBLIC_IP]

    return resolve


class Router:
    """Route table for httpx.MockTransport. Keys are full URLs (query
    included) or URL prefixes ending with '*'. Unmatched URLs return 404;
    robots.txt defaults to 404 (= no restrictions)."""

    def __init__(self, routes: dict | None = None):
        self.routes: dict = dict(routes or {})
        self.requests: list[httpx.Request] = []

    def add(self, url: str, response) -> None:
        self.routes[url] = response

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        response = self.routes.get(url)
        if response is None:
            for key, value in self.routes.items():
                if key.endswith("*") and url.startswith(key[:-1]):
                    response = value
                    break
        if response is None:
            return httpx.Response(404, text="not found", headers={"content-type": "text/html"})
        if callable(response):
            return response(request)
        if isinstance(response, httpx.Response):
            return response
        status, body = response if isinstance(response, tuple) else (200, response)
        return httpx.Response(status, text=body, headers={"content-type": "text/html; charset=utf-8"})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]


@pytest.fixture
def router() -> Router:
    return Router()


class FakeBrowser:
    """Stands in for BrowserPool: returns canned rendered HTML."""

    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.rendered: list[str] = []
        self.enabled = True

    async def render(self, url: str) -> tuple[str, str]:
        self.rendered.append(url)
        return url, self.pages[url]

    async def aclose(self) -> None:
        pass
