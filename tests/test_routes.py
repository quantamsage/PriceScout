"""HTTP routes, SSE progress and end-to-end search flow (all HTTP mocked)."""

import asyncio
import json
import time

import httpx
import pytest

from app.main import create_app
from tests.conftest import Router, load_fixture, make_resolver

JUMIA_URL = "https://www.jumia.co.ke/catalog/?q=Samsung+Galaxy+S24+Ultra+256GB"
AMAZON_URL = "https://www.amazon.com/s?k=Samsung+Galaxy+S24+Ultra+256GB"
EBAY_URL = "https://www.ebay.com/sch/i.html?_nkw=Samsung+Galaxy+S24+Ultra+256GB"
QUERY = "Samsung Galaxy S24 Ultra 256GB"


@pytest.fixture
def make_client(settings):
    async def _make(router: Router, **overrides):
        s = settings.model_copy(update=overrides) if overrides else settings
        app = create_app(s, transport=router.transport, resolver=make_resolver())
        return app

    return _make


@pytest.fixture
async def client(make_client, router):
    app = await make_client(router)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            c.app = app
            yield c


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.split("\n\n"):
        name, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if name:
            events.append((name, data))
    return events


async def wait_for_search(client):
    await client.app.state.search_manager.wait_idle()


# --- pages ---------------------------------------------------------------------

async def test_homepage_renders_search_form(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert 'name="query"' in response.text
    assert 'name="websites"' in response.text
    assert "Compare prices" in response.text
    assert "Content-Security-Policy" in response.headers


async def test_unknown_page_is_404_without_details(client):
    response = await client.get("/does-not-exist")
    assert response.status_code == 404
    assert "Traceback" not in response.text


async def test_unknown_search_is_404(client):
    assert (await client.get("/search/9999")).status_code == 404
    assert (await client.get("/api/searches/9999")).status_code == 404


# --- validation ------------------------------------------------------------------

@pytest.mark.parametrize(
    "form, message",
    [
        ({"query": "", "websites": ["https://www.jumia.co.ke"]}, "Enter the product"),
        ({"query": "iphone", "websites": [""]}, "at least one website"),
        ({"query": "iphone", "websites": ["http://127.0.0.1/"]}, "not allowed"),
        ({"query": "iphone", "websites": ["http://example.com:6379/"]}, "standard web ports"),
        ({"query": "iphone", "websites": ["ftp://example.com"]}, "Only http"),
        ({"query": "iphone", "websites": ["http://localhost/"]}, "not allowed"),
    ],
)
async def test_form_validation_errors(client, form, message):
    response = await client.post("/search", data=form)
    assert response.status_code == 422
    assert message in response.text
    assert client.app.state.search_manager.broker._channels == {}


async def test_too_many_websites_rejected(client):
    sites = [f"https://shop{i}.example.com" for i in range(20)]
    response = await client.post("/search", data={"query": "iphone", "websites": sites})
    assert response.status_code == 422
    assert "up to 8 websites" in response.text


async def test_validation_errors_escape_user_input(client):
    response = await client.post("/search", data={"query": "<script>alert(1)</script>", "websites": ["javascript:alert(1)"]})
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;" in response.text


async def test_api_validation(client):
    response = await client.post("/api/searches", json={"query": "x", "websites": ["http://10.0.0.1/"]})
    assert response.status_code == 422
    response = await client.post("/api/searches", json={"query": "x"})
    assert response.status_code == 422


# --- end-to-end search -----------------------------------------------------------

async def test_full_search_flow_with_partial_failure(client, router):
    router.add(JUMIA_URL, load_fixture("jumia_search.html"))
    router.add(AMAZON_URL, load_fixture("amazon_captcha.html"))
    router.add(EBAY_URL, load_fixture("ebay_search.html"))

    response = await client.post(
        "/search",
        data={"query": QUERY, "websites": ["https://www.jumia.co.ke", "https://www.amazon.com", "www.ebay.com"]},
    )
    assert response.status_code == 303
    location = response.headers["location"]
    search_id = int(location.rsplit("/", 1)[1])

    page = await client.get(location)
    assert page.status_code == 200
    assert QUERY in page.text
    assert "Jumia (jumia.co.ke)" in page.text

    await wait_for_search(client)
    data = (await client.get(f"/api/searches/{search_id}")).json()
    assert data["summary"]["status"] == "partial"
    assert data["summary"]["total_websites"] == 3
    assert data["summary"]["successful_websites"] == 2
    assert data["summary"]["failed_websites"] == 1

    sites = {w["domain"]: w for w in data["websites"]}
    assert sites["amazon.com"]["status"] == "failed"
    assert "blocked" in sites["amazon.com"]["message"].lower()
    assert sites["jumia.co.ke"]["status"] == "completed"
    assert sites["jumia.co.ke"]["product_count"] == 2

    listings = data["listings"]
    case = next(l for l in listings if "Case" in l["name"])
    assert case["is_match"] is False
    phone = next(l for l in listings if l["name"].startswith("Samsung Galaxy S24 Ultra, 6.8"))
    assert phone["is_match"] is True
    assert phone["price"] == "154999.00"
    assert phone["currency"] == "KES"
    # KES and USD listings with no exchange rates: nothing is ranked.
    assert data["summary"]["comparison_currency"] is None
    assert data["summary"]["cheapest_listing_id"] is None


async def test_sse_stream_replays_completed_search(client, router):
    router.add(JUMIA_URL, load_fixture("jumia_search.html"))
    response = await client.post("/api/searches", json={"query": QUERY, "websites": ["https://www.jumia.co.ke"]})
    assert response.status_code == 201
    created = response.json()
    await wait_for_search(client)

    # Live channel (still retained) and database replay must agree.
    live = parse_sse((await client.get(created["events_url"])).text)
    client.app.state.search_manager.broker._channels.clear()
    replay = parse_sse((await client.get(created["events_url"])).text)

    for events in (live, replay):
        names = [name for name, _ in events]
        assert names[0] == "start"
        assert names[-2:] == ["summary", "done"]
        completed = [d for n, d in events if n == "website" and d["status"] == "completed"]
        assert len(completed) == 1
        assert completed[0]["product_count"] == 2
        assert {l["name"] for l in completed[0]["listings"]} >= {"Silicone Case Cover For Samsung Galaxy S24 Ultra"}
        summary = dict(events)["summary"]
        assert summary["comparison_currency"] == "KES"
        assert summary["cheapest_listing_id"] is not None


async def test_sse_streams_progress_while_running(client, router):
    gate = asyncio.Event()

    async def slow_jumia(request):
        await gate.wait()
        return httpx.Response(200, text=load_fixture("jumia_search.html"), headers={"content-type": "text/html"})

    # MockTransport supports async handlers.
    router.add(JUMIA_URL, slow_jumia)
    created = (await client.post("/api/searches", json={"query": QUERY, "websites": ["https://www.jumia.co.ke"]})).json()

    async def read_stream():
        return (await client.get(created["events_url"])).text

    reader = asyncio.create_task(read_stream())
    await asyncio.sleep(0.2)
    gate.set()
    body = await asyncio.wait_for(reader, timeout=10)
    events = parse_sse(body)
    statuses = [d["status"] for n, d in events if n == "website"]
    assert statuses == ["searching", "completed"]
    assert events[-1][0] == "done"


async def test_concurrent_site_searches(client, router):
    """Sites are searched concurrently: total time ~ one site, not the sum."""
    delay = 0.5

    def make_slow(body):
        async def handler(request):
            await asyncio.sleep(delay)
            return httpx.Response(200, text=body, headers={"content-type": "text/html"})
        return handler

    router.add(JUMIA_URL, make_slow(load_fixture("jumia_search.html")))
    router.add(EBAY_URL, make_slow(load_fixture("ebay_search.html")))
    router.add(AMAZON_URL, make_slow(load_fixture("amazon_search.html")))

    started = time.perf_counter()
    created = (await client.post("/api/searches", json={
        "query": QUERY,
        "websites": ["https://www.jumia.co.ke", "https://www.ebay.com", "https://www.amazon.com"],
    })).json()
    await wait_for_search(client)
    elapsed = time.perf_counter() - started
    assert elapsed < delay * 2.5

    data = (await client.get(f"/api/searches/{created['id']}")).json()
    assert data["summary"]["successful_websites"] == 3


async def test_site_timeout_does_not_block_others(make_client, router):
    async def hang(request):
        await asyncio.sleep(30)

    router.add(EBAY_URL, hang)
    router.add(JUMIA_URL, load_fixture("jumia_search.html"))
    app = await make_client(router, site_timeout_seconds=0.5)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            created = (await c.post("/api/searches", json={
                "query": QUERY, "websites": ["https://www.ebay.com", "https://www.jumia.co.ke"],
            })).json()
            await app.state.search_manager.wait_idle()
            data = (await c.get(f"/api/searches/{created['id']}")).json()
    sites = {w["domain"]: w for w in data["websites"]}
    assert sites["ebay.com"]["status"] == "failed"
    assert sites["ebay.com"]["message"] == "Search timed out."
    assert sites["jumia.co.ke"]["status"] == "completed"


async def test_unexpected_adapter_crash_is_contained(client, router, monkeypatch):
    from app.scrapers.sites.jumia import JumiaAdapter

    async def explode(self, query, ctx):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(JumiaAdapter, "search", explode)
    router.add(EBAY_URL, load_fixture("ebay_search.html"))
    created = (await client.post("/api/searches", json={
        "query": QUERY, "websites": ["https://www.jumia.co.ke", "https://www.ebay.com"],
    })).json()
    await wait_for_search(client)
    data = (await client.get(f"/api/searches/{created['id']}")).json()
    sites = {w["domain"]: w for w in data["websites"]}
    assert sites["jumia.co.ke"]["status"] == "failed"
    assert "secret internal detail" not in json.dumps(data)
    assert sites["ebay.com"]["status"] == "completed"


async def test_conversion_with_configured_rates(make_client, router):
    router.add(JUMIA_URL, load_fixture("jumia_search.html"))
    router.add(EBAY_URL, load_fixture("ebay_search.html"))
    app = await make_client(router, exchange_rates=json.dumps({"USD": 1, "KES": 129.0}),
                            exchange_rates_as_of="2026-11-01T00:00:00Z")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            created = (await c.post("/api/searches", json={
                "query": QUERY, "websites": ["https://www.jumia.co.ke", "https://www.ebay.com"],
            })).json()
            await app.state.search_manager.wait_idle()
            data = (await c.get(f"/api/searches/{created['id']}")).json()
    assert data["summary"]["comparison_currency"] == "USD"
    kes = next(l for l in data["listings"] if l["currency"] == "KES" and l["is_match"])
    assert kes["converted_currency"] == "USD"
    assert kes["converted_price"] == "1201.54"  # 154999 / 129
    assert kes["exchange_rate_source"] == "configured rates"
    cheapest = next(l for l in data["listings"] if l["id"] == data["summary"]["cheapest_listing_id"])
    assert cheapest["price"] == "720.50"


async def test_history_lists_previous_searches(client, router):
    router.add(JUMIA_URL, load_fixture("jumia_search.html"))
    await client.post("/api/searches", json={"query": QUERY, "websites": ["https://www.jumia.co.ke"]})
    await client.post("/api/searches", json={"query": QUERY, "websites": ["https://www.jumia.co.ke"]})
    await wait_for_search(client)

    page = await client.get("/history")
    assert page.status_code == 200
    assert page.text.count(QUERY) == 2
    assert "jumia.co.ke" in page.text

    # Each search keeps its own listings (history is preserved, not overwritten).
    first = (await client.get("/api/searches/1")).json()
    second = (await client.get("/api/searches/2")).json()
    assert {l["id"] for l in first["listings"]}.isdisjoint({l["id"] for l in second["listings"]})


async def test_history_empty_state(client):
    page = await client.get("/history")
    assert "No searches yet" in page.text
