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
