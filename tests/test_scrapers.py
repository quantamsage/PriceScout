"""Adapter and HTML-extraction tests using local fixtures and mocked HTTP."""

from decimal import Decimal

import httpx
import pytest

from app.scrapers.base import SearchContext, UnsupportedWebsiteError
from app.scrapers.extraction import (
    extract_generic_cards,
    extract_json_ld,
    looks_blocked,
    looks_js_rendered,
    parse_html,
)
from app.scrapers.fetcher import BlockedError, FetchError, RobotsDisallowedError, SafeFetcher
from app.scrapers.generic import GenericAdapter
from app.scrapers.registry import resolve_adapter
from app.scrapers.sites.amazon import AmazonAdapter
from app.scrapers.sites.ebay import EbayAdapter
from app.scrapers.sites.jumia import JumiaAdapter
from app.scrapers.sites.kilimall import KilimallAdapter
from tests.conftest import FakeBrowser, load_fixture, make_resolver


@pytest.fixture
async def make_ctx(settings, router):
    fetchers = []

    def _make(browser=None, **overrides):
        s = settings.model_copy(update=overrides) if overrides else settings
        fetcher = SafeFetcher(s, transport=router.transport, resolver=make_resolver())
        fetchers.append(fetcher)
        return SearchContext(settings=s, fetcher=fetcher, browser=browser)

    yield _make
    for fetcher in fetchers:
        await fetcher.aclose()


# --- registry ---------------------------------------------------------------

@pytest.mark.parametrize(
    "url, adapter",
    [
        ("https://www.amazon.com/", AmazonAdapter),
        ("https://www.amazon.co.uk/", AmazonAdapter),
        ("https://www.ebay.com/", EbayAdapter),
        ("https://www.jumia.co.ke/", JumiaAdapter),
        ("https://www.kilimall.co.ke/", KilimallAdapter),
        ("https://shop.example.com/", GenericAdapter),
        ("https://notamazon.com/", GenericAdapter),
    ],
)
def test_registry_resolves_adapters(settings, url, adapter):
    assert type(resolve_adapter(url, settings)) is adapter


def test_registry_prefers_official_api_when_configured(settings):
    configured = settings.model_copy(update={"ebay_client_id": "id", "ebay_client_secret": "secret"})
    assert resolve_adapter("https://www.ebay.com/", configured).kind == "api"
    assert resolve_adapter("https://www.ebay.com/", settings).kind == "site"


# --- site-specific adapters --------------------------------------------------

async def test_amazon_adapter(router, make_ctx):
    router.add("https://www.amazon.com/s?k=samsung+galaxy+s24+ultra+256gb", load_fixture("amazon_search.html"))
    products = await AmazonAdapter("https://www.amazon.com/").search("samsung galaxy s24 ultra 256gb", make_ctx())
    assert len(products) == 3
    first = products[0]
    assert first.name.startswith("SAMSUNG Galaxy S24 Ultra 256GB")
    assert first.price == Decimal("1099.99")  # current price, not the struck-through one
    assert first.currency == "USD"
    assert first.url == "https://www.amazon.com/Samsung-Galaxy-S24-Ultra/dp/B0CMDRCZBJ/ref=sr_1_1"
    assert first.image_url == "https://m.media-amazon.com/images/I/s24u.jpg"
    assert first.extraction_method == "selectors"
    renewed = products[2]
    assert renewed.price is None  # unavailable, never zero
    assert renewed.availability == "out_of_stock"


async def test_amazon_captcha_is_reported_as_blocked(router, make_ctx):
    router.add("https://www.amazon.com/s?k=iphone", load_fixture("amazon_captcha.html"))
    with pytest.raises(BlockedError):
        await AmazonAdapter("https://www.amazon.com/").search("iphone", make_ctx())


async def test_ebay_adapter_skips_placeholder_and_reads_seller(router, make_ctx):
    router.add("https://www.ebay.com/sch/i.html?_nkw=galaxy+s24", load_fixture("ebay_search.html"))
    products = await EbayAdapter("https://www.ebay.com/").search("galaxy s24", make_ctx())
    assert [p.price for p in products] == [Decimal("849.00"), Decimal("720.50")]
    assert all("Shop on eBay" not in p.name for p in products)
    assert products[0].seller.startswith("phone_deals_usa")
    assert products[0].image_url == "https://i.ebayimg.com/images/g/abc/s-l300.jpg"


async def test_jumia_adapter_uses_kes_and_lazy_images(router, make_ctx):
    router.add("https://www.jumia.co.ke/catalog/?q=s24+ultra", load_fixture("jumia_search.html"))
    products = await JumiaAdapter("https://www.jumia.co.ke/").search("s24 ultra", make_ctx())
    assert len(products) == 2
    assert products[0].price == Decimal("154999")
    assert products[0].currency == "KES"
    assert products[0].image_url == "https://ke.jumia.is/unsafe/fit-in/300x300/s24u.jpg"
    assert products[0].url.startswith("https://www.jumia.co.ke/samsung-galaxy-s24-ultra")


async def test_kilimall_falls_back_to_browser_for_js_pages(router, make_ctx):
    url = "https://www.kilimall.co.ke/search?q=s24+ultra"
    router.add(url, load_fixture("kilimall_shell.html"))
    browser = FakeBrowser({url: load_fixture("kilimall_rendered.html")})
    products = await KilimallAdapter("https://www.kilimall.co.ke/").search("s24 ultra", make_ctx(browser=browser))
    assert browser.rendered == [url]
    assert len(products) == 1
    assert products[0].price == Decimal("149500")
    assert products[0].currency == "KES"
    assert products[0].extraction_method.endswith("+browser")


async def test_browser_not_used_when_static_html_is_enough(router, make_ctx):
    router.add("https://www.jumia.co.ke/catalog/?q=s24", load_fixture("jumia_search.html"))
    browser = FakeBrowser({})
    await JumiaAdapter("https://www.jumia.co.ke/").search("s24", make_ctx(browser=browser))
    assert browser.rendered == []


async def test_js_site_without_browser_reports_clear_error(router, make_ctx):
    router.add("https://www.kilimall.co.ke/search?q=s24", load_fixture("kilimall_shell.html"))
    with pytest.raises(FetchError, match="JavaScript"):
        await KilimallAdapter("https://www.kilimall.co.ke/").search("s24", make_ctx(browser=None))


# --- generic adapter ---------------------------------------------------------

async def test_generic_adapter_uses_homepage_search_form(router, make_ctx):
    router.add("https://laptops.example.com/", load_fixture("generic_home.html"))
    router.add("https://laptops.example.com/find?cat=all&term=dell+xps+15", load_fixture("generic_cards.html"))
    products = await GenericAdapter("https://laptops.example.com/").search("dell xps 15", make_ctx())
    assert len(products) == 2
    xps = products[0]
    assert xps.name == "Dell XPS 15 9530 Laptop, 32GB RAM, 1TB SSD"
    assert xps.price == Decimal("1899.00")  # not the <del> price
    assert xps.currency == "USD"
    assert xps.url == "https://laptops.example.com/item/dell-xps-15-9530"
    assert xps.image_url == "https://laptops.example.com/img/xps15.jpg"
    assert xps.availability == "in_stock"
    assert products[1].availability == "out_of_stock"
    assert products[1].image_url == "https://laptops.example.com/img/xps15-old.jpg"


async def test_generic_adapter_query_placeholder(router, make_ctx):
    router.add("https://shop.example.com/s?term=mx+master+3s", load_fixture("generic_jsonld.html"))
    adapter = GenericAdapter("https://shop.example.com/s?term={query}")
    products = await adapter.search("mx master 3s", make_ctx())
    assert router.urls() == ["https://shop.example.com/robots.txt", "https://shop.example.com/s?term=mx+master+3s"]
    assert products[0].extraction_method == "json-ld"


async def test_generic_adapter_tries_common_patterns(router, make_ctx):
    router.add("https://store.example.com/", "<html><body><p>Welcome</p></body></html>")
    router.add("https://store.example.com/search?type=product&q=mx+master", load_fixture("generic_jsonld.html"))
    products = await GenericAdapter("https://store.example.com/").search("mx master", make_ctx())
    assert len(products) == 3
    assert "https://store.example.com/search?q=mx+master" in router.urls()  # tried first, 404


async def test_generic_adapter_no_results_returns_empty(router, make_ctx):
    router.add("https://empty.example.com/*", "<html><body><p>Nothing here</p></body></html>")
    products = await GenericAdapter("https://empty.example.com/").search("anything", make_ctx())
    assert products == []


async def test_generic_adapter_unreachable(router, make_ctx):
    def boom(request):
        raise httpx.ConnectError("refused")

    router.add("https://down.example.com/*", boom)
    with pytest.raises(FetchError):
        await GenericAdapter("https://down.example.com/").search("x", make_ctx())


# --- extraction --------------------------------------------------------------

def test_json_ld_extraction_handles_itemlist_offers_and_bad_json():
    soup = parse_html(load_fixture("generic_jsonld.html"))
    products = extract_json_ld(soup, "https://shop.example.com/search", "shop.example.com")
    assert [p.name for p in products] == [
        "Logitech MX Master 3S Wireless Mouse",
        "Logitech MX Master 3S for Mac",
        "Logitech MX Anywhere 3",
    ]
    assert products[0].price == Decimal("99.99")
    assert products[0].currency == "EUR"
    assert products[0].seller == "TechShop"
    assert products[0].availability == "in_stock"
    assert products[0].url == "https://shop.example.com/products/mx-master-3s"
    assert products[1].price == Decimal("109.00")  # AggregateOffer lowPrice
    assert products[1].availability == "out_of_stock"
    assert products[2].price is None


def test_malformed_html_does_not_crash():
    soup = parse_html(load_fixture("malformed.html"))
    products = extract_generic_cards(soup, "https://bad.example.com/", "bad.example.com")
    for product in products:
        assert product.image_url is None or product.image_url.startswith("http")
    assert extract_json_ld(soup, "https://bad.example.com/", "bad.example.com") == []


def test_page_classification():
    captcha = load_fixture("amazon_captcha.html")
    assert looks_blocked(parse_html(captcha), captcha)
    normal = load_fixture("jumia_search.html")
    assert not looks_blocked(parse_html(normal), normal)
    assert looks_js_rendered(parse_html(load_fixture("kilimall_shell.html")))
    assert not looks_js_rendered(parse_html(normal))


# --- fetcher behaviour -------------------------------------------------------

async def test_robots_txt_disallow_is_respected(router, make_ctx):
    router.add("https://www.jumia.co.ke/robots.txt", "User-agent: *\nDisallow: /catalog/")
    with pytest.raises(RobotsDisallowedError):
        await JumiaAdapter("https://www.jumia.co.ke/").search("s24", make_ctx())
    assert "https://www.jumia.co.ke/catalog/?q=s24" not in router.urls()


@pytest.mark.parametrize("status, error", [(403, BlockedError), (429, BlockedError), (503, FetchError)])
async def test_http_errors_map_to_user_messages(router, make_ctx, status, error):
    router.add("https://www.ebay.com/sch/i.html?_nkw=x", (status, "nope"))
    with pytest.raises(error) as info:
        await EbayAdapter("https://www.ebay.com/").search("x", make_ctx())
    assert "Traceback" not in info.value.user_message


async def test_timeout_is_reported(router, make_ctx):
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    router.add("https://www.ebay.com/sch/i.html?_nkw=x", slow)
    with pytest.raises(FetchError, match="too long"):
        await EbayAdapter("https://www.ebay.com/").search("x", make_ctx())


async def test_response_size_limit(router, make_ctx):
    router.add("https://www.ebay.com/sch/i.html?_nkw=x", "<html>" + "x" * 20_000 + "</html>")
    with pytest.raises(FetchError, match="too large"):
        await EbayAdapter("https://www.ebay.com/").search("x", make_ctx(max_response_bytes=10_000))


async def test_binary_content_is_refused(router, make_ctx):
    router.add(
        "https://www.ebay.com/sch/i.html?_nkw=x",
        httpx.Response(200, content=b"MZ\x90\x00", headers={"content-type": "application/octet-stream"}),
    )
    with pytest.raises(FetchError, match="content type"):
        await EbayAdapter("https://www.ebay.com/").search("x", make_ctx())


async def test_results_are_capped_per_site(router, make_ctx):
    items = "".join(
        f'<article class="prd"><a class="core" href="/p{i}.html"><h3 class="name">Phone {i}</h3>'
        f'<div class="prc">KSh {1000 + i}</div></a></article>'
        for i in range(40)
    )
    router.add("https://www.jumia.co.ke/catalog/?q=phone", f"<html><body>{items}</body></html>")
    products = await JumiaAdapter("https://www.jumia.co.ke/").search("phone", make_ctx(max_results_per_site=5))
    assert len(products) == 5


async def test_unsupported_when_no_search_urls(make_ctx):
    class NoUrls(GenericAdapter):
        async def search_urls(self, query, ctx):
            return []

    with pytest.raises(UnsupportedWebsiteError):
        await NoUrls("https://x.example.com/").search("q", make_ctx())


async def test_amazon_current_layout_combines_brand_and_title(router, make_ctx):
    router.add("https://www.amazon.com/s?k=galaxy+a15", load_fixture("amazon_search_v2.html"))
    products = await AmazonAdapter("https://www.amazon.com/").search("galaxy a15", make_ctx())
    assert products[0].name.startswith("Samsung Galaxy A15 5G (128GB, 2024)")
    assert products[0].price == Decimal("19355.69")
    assert products[0].currency == "KES"  # currency shown on the page wins over the TLD default
    assert products[0].url.endswith("/dp/B0FKNRD1XD/ref=sr_1_1")


def test_heuristics_do_not_override_site_selectors():
    # Extra price-like text outside product cards must not displace selector results.
    html = load_fixture("amazon_search.html").replace(
        "</body>", "<div><a href='/offers/1'>$12.00 (2+ used &amp; new offers)</a></div>"
                   "<div><a href='/offers/2'>$13.00 (2+ used &amp; new offers)</a></div>"
                   "<div><a href='/offers/3'>$14.00 (3 used &amp; new offers)</a></div>"
                   "<div><a href='/offers/4'>$15.00 (4 used &amp; new offers)</a></div></body>"
    )
    products = AmazonAdapter("https://www.amazon.com/").extract(parse_html(html), "https://www.amazon.com/s")
    assert {p.extraction_method for p in products} == {"selectors"}
