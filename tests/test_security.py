"""URL validation and SSRF protection."""

import httpcore
import httpx
import pytest

from app.scrapers.fetcher import FetchError, GuardedNetworkBackend, SafeFetcher
from app.services.url_safety import (
    UnsafeURLError,
    check_url_syntax,
    is_public_ip,
    normalize_website_url,
    validate_url,
)
from tests.conftest import make_resolver


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("amazon.com", "https://amazon.com/"),
        ("https://www.Jumia.co.ke", "https://www.jumia.co.ke/"),
        ("http://shop.example.com/search?q={query}", "http://shop.example.com/search?q={query}"),
    ],
)
def test_normalize_accepts_public_websites(raw, expected):
    assert normalize_website_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "ftp://example.com/",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://localhost/",
        "http://localhost:8000/admin",
        "http://foo.localhost/",
        "http://printer.local/",
        "http://intranet/",
        "http://127.0.0.1/",
        "http://10.0.0.5/",
        "http://172.16.3.4/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://100.64.0.1/",  # CGNAT
        "http://0.0.0.0/",
        "http://[::1]/",
        "http://[fe80::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://2130706433/",  # decimal 127.0.0.1
        "http://0x7f.1/",  # hex/short form
        "http://017700000001/",  # octal
        "http://user:pass@example.com/",
        "http://example.com:22/",
        "",
    ],
)
def test_normalize_rejects_unsafe_urls(raw):
    with pytest.raises(UnsafeURLError):
        normalize_website_url(raw)


def test_public_ip_classification():
    assert is_public_ip("93.184.216.34")
    assert is_public_ip("2606:2800:220:1:248:1893:25c8:1946")
    for ip in ("127.0.0.1", "10.1.2.3", "192.168.0.1", "169.254.1.1", "::1", "fc00::1", "224.0.0.1", "100.100.1.1"):
        assert not is_public_ip(ip), ip


async def test_dns_resolving_to_private_ip_is_rejected():
    resolver = make_resolver({"evil.example.com": ["10.0.0.7"]})
    with pytest.raises(UnsafeURLError):
        await validate_url("https://evil.example.com/", resolver)


async def test_dns_with_any_private_record_is_rejected():
    resolver = make_resolver({"mixed.example.com": ["93.184.216.34", "127.0.0.1"]})
    with pytest.raises(UnsafeURLError):
        await validate_url("https://mixed.example.com/", resolver)


async def test_unresolvable_host_is_rejected():
    async def failing(host, port):
        raise OSError("NXDOMAIN")

    with pytest.raises(UnsafeURLError):
        await validate_url("https://nope.example.com/", failing)


def test_syntax_check_returns_host():
    assert check_url_syntax("https://www.example.com/x") == "www.example.com"


# --- redirects are re-validated ------------------------------------------

async def test_redirect_to_internal_address_is_blocked(settings):
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.host == "shop.example.com":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
        return httpx.Response(200, text="SECRET")

    async with SafeFetcher(settings, transport=httpx.MockTransport(handler), resolver=make_resolver()) as fetcher:
        with pytest.raises(FetchError, match="(?i)private or internal"):
            await fetcher.get("https://shop.example.com/", check_robots=False)
    assert requested == ["https://shop.example.com/"]


async def test_redirect_to_host_resolving_internally_is_blocked(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "shop.example.com":
            return httpx.Response(301, headers={"location": "https://rebind.example.net/"})
        return httpx.Response(200, text="SECRET")

    resolver = make_resolver({"rebind.example.net": ["192.168.0.10"]})
    async with SafeFetcher(settings, transport=httpx.MockTransport(handler), resolver=resolver) as fetcher:
        with pytest.raises(FetchError):
            await fetcher.get("https://shop.example.com/", check_robots=False)


async def test_safe_redirect_is_followed(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        return httpx.Response(200, text="<html>ok</html>", headers={"content-type": "text/html"})

    async with SafeFetcher(settings, transport=httpx.MockTransport(handler), resolver=make_resolver()) as fetcher:
        result = await fetcher.get("https://shop.example.com/old", check_robots=False)
    assert result.url == "https://shop.example.com/new"
    assert result.text == "<html>ok</html>"


async def test_redirect_loops_are_bounded(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/again"})

    async with SafeFetcher(settings, transport=httpx.MockTransport(handler), resolver=make_resolver()) as fetcher:
        with pytest.raises(FetchError, match="redirected too many times"):
            await fetcher.get("https://shop.example.com/", check_robots=False)


# --- connection-level guard (DNS rebinding) ---------------------------------

async def test_guarded_backend_refuses_private_ip_at_connect_time():
    calls = []

    class RecordingBackend(httpcore.AsyncNetworkBackend):
        async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            calls.append(host)
            raise AssertionError("must not connect")

    backend = GuardedNetworkBackend(make_resolver({"rebind.example.com": ["127.0.0.1"]}), RecordingBackend())
    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("rebind.example.com", 443)
    assert calls == []


async def test_guarded_backend_connects_to_validated_ip():
    calls = []

    class RecordingBackend(httpcore.AsyncNetworkBackend):
        async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            calls.append((host, port))
            return object()

    backend = GuardedNetworkBackend(make_resolver(), RecordingBackend())
    await backend.connect_tcp("shop.example.com", 443)
    assert calls == [("93.184.216.34", 443)]  # pinned to the checked address
