from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.database import create_engine, create_session_factory, init_db
from app.models import PriceSnapshot, ProductListing, Search, SearchWebsite


@pytest.fixture
async def session_factory(settings):
    engine = create_engine(settings.database_url)
    await init_db(engine)
    yield create_session_factory(engine)
    await engine.dispose()


async def test_foreign_keys_are_enforced(session_factory):
    async with session_factory() as session:
        result = await session.execute(text("PRAGMA foreign_keys"))
        assert result.scalar() == 1
        session.add(SearchWebsite(search_id=999, url="https://x.com/", domain="x.com", display_name="x"))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_models_round_trip_decimal_prices(session_factory):
    async with session_factory() as session, session.begin():
        search = Search(query="iphone 16", total_websites=1)
        site = SearchWebsite(url="https://shop.com/", domain="shop.com", display_name="shop.com")
        search.websites.append(site)
        session.add(search)
        await session.flush()
        listing = ProductListing(
            search_id=search.id, search_website_id=site.id, name="iPhone 16",
            url="https://shop.com/p/1", canonical_url="https://shop.com/p/1",
            extraction_method="json-ld",
        )
        listing.snapshots.append(PriceSnapshot(price=Decimal("999.99"), currency="USD"))
        listing.snapshots.append(PriceSnapshot(price=None, currency=None))
        session.add(listing)

    async with session_factory() as session:
        snapshots = (await session.execute(select(PriceSnapshot).order_by(PriceSnapshot.id))).scalars().all()
        assert snapshots[0].price == Decimal("999.99")
        assert snapshots[1].price is None  # unavailable, not zero
        assert snapshots[0].retrieved_at.tzinfo is not None


# --- repository ------------------------------------------------------------------

from app.models import SearchStatus, WebsiteStatus  # noqa: E402
from app.repositories.search_repository import NewListing, NewWebsite, SearchRepository, canonical_url  # noqa: E402


def _listing(name, url, price):
    return NewListing(
        name=name, url=url, image_url=None, seller=None, availability="unknown",
        extraction_method="selectors", match_score=0.9, is_match=True,
        price=Decimal(price) if price else None, currency="KES" if price else None, raw_price_text=price,
    )


@pytest.fixture
async def repo(session_factory):
    return SearchRepository(session_factory)


async def test_repository_preserves_price_history(repo):
    site = NewWebsite(url="https://www.jumia.co.ke/", domain="jumia.co.ke", display_name="Jumia", adapter="Jumia")
    url = "https://www.jumia.co.ke/p/1.html?utm=a"
    for price in ("150000", "145000"):
        search = await repo.create_search("s24", [site])
        await repo.add_listings(search.id, search.websites[0].id, [_listing("S24", url, price)])

    history = await repo.price_history("https://www.jumia.co.ke/p/1.html")
    assert [s.price for s in history] == [Decimal("150000"), Decimal("145000")]
    assert canonical_url(url) == "https://www.jumia.co.ke/p/1.html"
    searches, total = await repo.list_searches()
    assert total == 2
    assert searches[0].id > searches[1].id  # newest first


async def test_add_listings_and_finalize(repo):
    site = NewWebsite(url="https://shop.example.com/", domain="shop.example.com", display_name="shop", adapter="Generic")
    search = await repo.create_search("mx master", [site])
    website_id = search.websites[0].id
    rows = await repo.add_listings(search.id, website_id, [_listing("A", "https://shop.example.com/a", "10"),
                                                           _listing("B", "https://shop.example.com/b", None)])
    assert rows[1].snapshots[0].price is None
    await repo.update_website(website_id, status=WebsiteStatus.COMPLETED, message="2 found", product_count=2)
    await repo.finalize_search(search.id, status=SearchStatus.COMPLETED, successful=1, failed=0, matching=2,
                               comparison_currency="KES", group_keys={rows[0].id: "g1", rows[1].id: "g2"})
    stored = await repo.get_search(search.id)
    assert stored.status == SearchStatus.COMPLETED
    assert stored.completed_at is not None
    assert {l.group_key for l in stored.listings} == {"g1", "g2"}
    assert stored.websites[0].finished_at is not None


async def test_interrupted_searches_are_marked_failed(repo):
    site = NewWebsite(url="https://a.example.com/", domain="a.example.com", display_name="a", adapter="Generic")
    running = await repo.create_search("q", [site])
    await repo.mark_search_running(running.id, None)
    other = await repo.create_search("q2", [site])
    await repo.update_website(other.websites[0].id, status=WebsiteStatus.COMPLETED)
    await repo.finalize_search(other.id, status=SearchStatus.COMPLETED, successful=1, failed=0, matching=0,
                               comparison_currency=None, group_keys={})

    assert await repo.fail_unfinished_searches() == 1
    failed = await repo.get_search(running.id)
    assert failed.status == SearchStatus.FAILED
    assert failed.websites[0].message == "Search was interrupted."
    assert failed.failed_websites == 1
    assert (await repo.get_search(other.id)).status == SearchStatus.COMPLETED


async def test_fail_search_only_affects_that_search(repo):
    site = NewWebsite(url="https://a.example.com/", domain="a.example.com", display_name="a", adapter="Generic")
    first = await repo.create_search("q", [site])
    second = await repo.create_search("q", [site])
    await repo.fail_search(first.id)
    assert (await repo.get_search(first.id)).status == SearchStatus.FAILED
    assert (await repo.get_search(second.id)).status == SearchStatus.PENDING


async def test_deleting_search_cascades(session_factory, repo):
    site = NewWebsite(url="https://a.example.com/", domain="a.example.com", display_name="a", adapter="Generic")
    search = await repo.create_search("q", [site])
    await repo.add_listings(search.id, search.websites[0].id, [_listing("A", "https://a.example.com/a", "5")])
    async with session_factory() as session, session.begin():
        await session.execute(text("DELETE FROM searches WHERE id = :id"), {"id": search.id})
    async with session_factory() as session:
        assert (await session.execute(text("SELECT COUNT(*) FROM price_snapshots"))).scalar() == 0
