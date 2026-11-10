"""Data access for searches, websites, listings and price snapshots.

All database work lives here; services call these functions and never
build queries themselves. Each public function runs in its own
transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.models import PriceSnapshot, ProductListing, Search, SearchStatus, SearchWebsite, WebsiteStatus
from app.models.types import utcnow


@dataclass(frozen=True)
class NewWebsite:
    url: str
    domain: str
    display_name: str
    adapter: str


@dataclass(frozen=True)
class NewListing:
    name: str
    url: str
    image_url: Optional[str]
    seller: Optional[str]
    availability: str
    extraction_method: str
    match_score: float
    is_match: bool
    price: Optional[Decimal]
    currency: Optional[str]
    raw_price_text: Optional[str]
    converted_price: Optional[Decimal] = None
    converted_currency: Optional[str] = None
    exchange_rate: Optional[Decimal] = None
    exchange_rate_source: Optional[str] = None
    exchange_rate_as_of: Optional[datetime] = None
    retrieved_at: Optional[datetime] = None


def canonical_url(url: str) -> str:
    """Strip query/fragment so the same listing can be tracked over time."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/") or "/", "", ""))


class SearchRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def create_search(self, query: str, websites: list[NewWebsite]) -> Search:
        async with self._sessions() as session, session.begin():
            search = Search(query=query, status=SearchStatus.PENDING, total_websites=len(websites))
            search.websites = [
                SearchWebsite(
                    position=index,
                    url=site.url,
                    domain=site.domain,
                    display_name=site.display_name,
                    adapter=site.adapter,
                    status=WebsiteStatus.PENDING,
                )
                for index, site in enumerate(websites)
            ]
            session.add(search)
        return await self.get_search(search.id)  # type: ignore[return-value]

    async def mark_search_running(self, search_id: int, comparison_currency: Optional[str]) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(
                update(Search)
                .where(Search.id == search_id)
                .values(status=SearchStatus.RUNNING, comparison_currency=comparison_currency)
            )

    async def update_website(
        self,
        website_id: int,
        *,
        status: str,
        message: Optional[str] = None,
        product_count: Optional[int] = None,
    ) -> None:
        values: dict = {"status": status, "message": message}
        if status == WebsiteStatus.SEARCHING:
            values["started_at"] = utcnow()
        if status in (WebsiteStatus.COMPLETED, WebsiteStatus.FAILED):
            values["finished_at"] = utcnow()
        if product_count is not None:
            values["product_count"] = product_count
        async with self._sessions() as session, session.begin():
            await session.execute(update(SearchWebsite).where(SearchWebsite.id == website_id).values(**values))

    async def add_listings(
        self, search_id: int, website_id: int, listings: list[NewListing]
    ) -> list[ProductListing]:
        """Insert listings with their price snapshots in one transaction."""
        async with self._sessions() as session, session.begin():
            rows: list[ProductListing] = []
            for item in listings:
                row = ProductListing(
                    search_id=search_id,
                    search_website_id=website_id,
                    name=item.name,
                    url=item.url,
                    canonical_url=canonical_url(item.url),
                    image_url=item.image_url,
                    seller=item.seller,
                    availability=item.availability,
                    extraction_method=item.extraction_method,
                    match_score=item.match_score,
                    is_match=item.is_match,
                )
                row.snapshots = [
                    PriceSnapshot(
                        price=item.price,
                        currency=item.currency,
                        raw_price_text=item.raw_price_text,
                        converted_price=item.converted_price,
                        converted_currency=item.converted_currency,
                        exchange_rate=item.exchange_rate,
                        exchange_rate_source=item.exchange_rate_source,
                        exchange_rate_as_of=item.exchange_rate_as_of,
                        retrieved_at=item.retrieved_at or utcnow(),
                    )
                ]
                session.add(row)
                rows.append(row)
            await session.flush()
            ids = [row.id for row in rows]
        if not ids:
            return []
        async with self._sessions() as session:
            result = await session.execute(
                select(ProductListing)
                .where(ProductListing.id.in_(ids))
                .options(selectinload(ProductListing.snapshots), selectinload(ProductListing.website))
                .order_by(ProductListing.id)
            )
            return list(result.scalars())

    async def finalize_search(
        self,
        search_id: int,
        *,
        status: str,
        successful: int,
        failed: int,
        matching: int,
        comparison_currency: Optional[str],
        group_keys: dict[int, str],
    ) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(
                update(Search)
                .where(Search.id == search_id)
                .values(
                    status=status,
                    successful_websites=successful,
                    failed_websites=failed,
                    matching_products=matching,
                    comparison_currency=comparison_currency,
                    completed_at=utcnow(),
                )
            )
            for listing_id, key in group_keys.items():
                await session.execute(
                    update(ProductListing).where(ProductListing.id == listing_id).values(group_key=key)
                )

    async def fail_unfinished_searches(self, search_id: Optional[int] = None) -> int:
        """Mark unfinished searches (all, or just `search_id`) as failed —
        used at startup for searches interrupted by a restart, and when a
        running search is cancelled."""
        unfinished = (SearchStatus.PENDING, SearchStatus.RUNNING)
        targets = select(Search.id).where(Search.status.in_(unfinished))
        if search_id is not None:
            targets = targets.where(Search.id == search_id)
        async with self._sessions() as session, session.begin():
            ids = list((await session.execute(targets)).scalars())
            if not ids:
                return 0
            await session.execute(
                update(SearchWebsite)
                .where(SearchWebsite.search_id.in_(ids))
                .where(SearchWebsite.status.in_((WebsiteStatus.PENDING, WebsiteStatus.SEARCHING)))
                .values(status=WebsiteStatus.FAILED, message="Search was interrupted.", finished_at=utcnow())
            )
            failed_counts = (
                select(func.count(SearchWebsite.id))
                .where(SearchWebsite.search_id == Search.id)
                .where(SearchWebsite.status == WebsiteStatus.FAILED)
                .scalar_subquery()
            )
            done_counts = (
                select(func.count(SearchWebsite.id))
                .where(SearchWebsite.search_id == Search.id)
                .where(SearchWebsite.status == WebsiteStatus.COMPLETED)
                .scalar_subquery()
            )
            await session.execute(
                update(Search)
                .where(Search.id.in_(ids))
                .values(
                    status=SearchStatus.FAILED,
                    completed_at=utcnow(),
                    failed_websites=failed_counts,
                    successful_websites=done_counts,
                )
            )
            return len(ids)

    async def fail_search(self, search_id: int) -> None:
        await self.fail_unfinished_searches(search_id)

    async def get_search(self, search_id: int) -> Optional[Search]:
        async with self._sessions() as session:
            result = await session.execute(
                select(Search)
                .where(Search.id == search_id)
                .options(
                    selectinload(Search.websites),
                    selectinload(Search.listings).selectinload(ProductListing.snapshots),
                    selectinload(Search.listings).selectinload(ProductListing.website),
                )
            )
            return result.scalar_one_or_none()

    async def list_searches(self, *, limit: int = 20, offset: int = 0) -> tuple[list[Search], int]:
        async with self._sessions() as session:
            total = await session.scalar(select(func.count(Search.id)))
            result = await session.execute(
                select(Search)
                .options(selectinload(Search.websites))
                .order_by(Search.created_at.desc(), Search.id.desc())
                .limit(limit)
                .offset(offset)
            )
            return list(result.scalars()), int(total or 0)

    async def price_history(self, url: str) -> list[PriceSnapshot]:
        """All snapshots recorded for a listing URL across searches."""
        async with self._sessions() as session:
            result = await session.execute(
                select(PriceSnapshot)
                .join(ProductListing)
                .where(ProductListing.canonical_url == canonical_url(url))
                .order_by(PriceSnapshot.retrieved_at)
            )
            return list(result.scalars())
