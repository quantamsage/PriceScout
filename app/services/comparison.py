"""Runs a comparison: searches all websites concurrently, scores and stores
results, and publishes progress events as each website finishes.

This module knows nothing about individual websites — it only uses the
adapter interface returned by the registry.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Optional

from app.config import Settings
from app.models import SearchStatus, WebsiteStatus
from app.repositories.search_repository import NewListing, SearchRepository
from app.schemas.product import Product
from app.scrapers.base import SearchContext
from app.scrapers.browser import BrowserPool
from app.scrapers.fetcher import FetchError, SafeFetcher
from app.scrapers.registry import resolve_adapter
from app.services.comparison_rules import compute_comparison
from app.services.currency import CurrencyService, ExchangeRates, convert
from app.services.product_matching import group_listings, relevance
from app.services.search_events import SearchEvent, SearchEventBroker
from app.services.serialization import comparable, dump, listing_out

logger = logging.getLogger(__name__)

FetcherFactory = Callable[[], AbstractAsyncContextManager[SafeFetcher]]


@dataclass(frozen=True)
class WebsiteJob:
    id: int
    url: str
    display_name: str


class ComparisonService:
    def __init__(
        self,
        settings: Settings,
        repository: SearchRepository,
        broker: SearchEventBroker,
        fetcher_factory: FetcherFactory,
        currency: CurrencyService,
        browser: Optional[BrowserPool] = None,
    ) -> None:
        self._settings = settings
        self._repo = repository
        self._broker = broker
        self._fetcher_factory = fetcher_factory
        self._currency = currency
        self._browser = browser

    def _publish(self, search_id: int, type_: str, data: dict) -> None:
        self._broker.publish(search_id, SearchEvent(type_, data))

    async def run(self, search_id: int, query: str, websites: list[WebsiteJob]) -> None:
        self._broker.open(search_id)
        try:
            async with self._fetcher_factory() as fetcher:
                rates = await self._load_rates(fetcher)
                await self._repo.mark_search_running(search_id, rates.base if rates else None)
                self._publish(search_id, "start", {
                    "search_id": search_id,
                    "query": query,
                    "comparison": _rates_payload(rates),
                })
                ctx = SearchContext(settings=self._settings, fetcher=fetcher, browser=self._browser)
                semaphore = asyncio.Semaphore(self._settings.max_concurrent_sites)
                outcomes = await asyncio.gather(
                    *(self._search_website(search_id, query, job, ctx, rates, semaphore) for job in websites),
                    return_exceptions=True,
                )
            await self._finish(search_id, outcomes, rates)
        except asyncio.CancelledError:
            logger.info("Search %s cancelled", search_id)
            await self._safe_finalize_failure(search_id)
            raise
        except Exception:
            logger.exception("Search %s crashed", search_id)
            await self._safe_finalize_failure(search_id)
        finally:
            self._publish(search_id, "done", {})
            self._broker.close(search_id)

    async def _load_rates(self, fetcher: SafeFetcher) -> Optional[ExchangeRates]:
        try:
            return await self._currency.get_rates(fetcher)
        except Exception:
            logger.exception("Could not load exchange rates")
            return None

    async def _search_website(
        self,
        search_id: int,
        query: str,
        job: WebsiteJob,
        ctx: SearchContext,
        rates: Optional[ExchangeRates],
        semaphore: asyncio.Semaphore,
    ) -> bool:
        """Search one website. Never raises (except cancellation); returns success."""
        async with semaphore:
            await self._repo.update_website(job.id, status=WebsiteStatus.SEARCHING)
            self._publish(search_id, "website", {"id": job.id, "status": WebsiteStatus.SEARCHING})
            adapter = resolve_adapter(job.url, self._settings)
            try:
                products = await asyncio.wait_for(
                    adapter.search(query, ctx), timeout=self._settings.site_timeout_seconds
                )
            except asyncio.TimeoutError:
                return await self._fail(search_id, job, "Search timed out.")
            except FetchError as exc:
                logger.info("Website %s failed: %s", job.url, exc.user_message)
                return await self._fail(search_id, job, exc.user_message)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Adapter %s crashed for %s", adapter.name, job.url)
                return await self._fail(search_id, job, "An unexpected error occurred while searching this website.")

            new_listings = [self._to_new_listing(query, p, rates) for p in products]
            rows = await self._repo.add_listings(search_id, job.id, new_listings)
            matches = sum(1 for r in rows if r.is_match)
            message = (
                f"{len(rows)} products found ({matches} matching)" if rows else "No products found."
            )
            await self._repo.update_website(
                job.id, status=WebsiteStatus.COMPLETED, message=message, product_count=len(rows)
            )
            self._publish(search_id, "website", {
                "id": job.id,
                "status": WebsiteStatus.COMPLETED,
                "message": message,
                "product_count": len(rows),
                "matching_count": matches,
                "listings": [dump(listing_out(r)) for r in rows],
            })
            return True

    async def _fail(self, search_id: int, job: WebsiteJob, message: str) -> bool:
        await self._repo.update_website(job.id, status=WebsiteStatus.FAILED, message=message, product_count=0)
        self._publish(search_id, "website", {"id": job.id, "status": WebsiteStatus.FAILED, "message": message})
        return False

    def _to_new_listing(self, query: str, product: Product, rates: Optional[ExchangeRates]) -> NewListing:
        score = relevance(query, product.name)
        conversion = convert(product.price, product.currency, rates)
        return NewListing(
            name=product.name,
            url=product.url,
            image_url=product.image_url,
            seller=product.seller,
            availability=product.availability,
            extraction_method=product.extraction_method,
            match_score=score,
            is_match=score >= self._settings.match_threshold,
            price=product.price,
            currency=product.currency,
            raw_price_text=product.raw_price_text,
            converted_price=conversion.amount if conversion else None,
            converted_currency=conversion.currency if conversion else None,
            exchange_rate=conversion.rate if conversion else None,
            exchange_rate_source=conversion.source if conversion else None,
            exchange_rate_as_of=conversion.as_of if conversion else None,
        )

    async def _finish(self, search_id: int, outcomes: list, rates: Optional[ExchangeRates]) -> None:
        successful = sum(1 for o in outcomes if o is True)
        failed = len(outcomes) - successful
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                logger.error("Website task error in search %s: %r", search_id, outcome)

        search = await self._repo.get_search(search_id)
        listings = [listing_out(l) for l in search.listings] if search else []
        matching = [l for l in listings if l.is_match]
        groups = group_listings((l.id, l.name) for l in matching)
        comparison = compute_comparison(comparable(listings), rates.base if rates else None)

        if successful == 0 and outcomes:
            status = SearchStatus.FAILED
        elif failed:
            status = SearchStatus.PARTIAL
        else:
            status = SearchStatus.COMPLETED
        await self._repo.finalize_search(
            search_id,
            status=status,
            successful=successful,
            failed=failed,
            matching=len(matching),
            comparison_currency=comparison.currency,
            group_keys=groups,
        )
        self._publish(search_id, "summary", {
            "status": status,
            "total_websites": len(outcomes),
            "successful_websites": successful,
            "failed_websites": failed,
            "matching_products": len(matching),
            "comparison_currency": comparison.currency,
            "cheapest_listing_id": comparison.cheapest_id,
            "groups": {str(k): v for k, v in groups.items()},
        })

    async def _safe_finalize_failure(self, search_id: int) -> None:
        try:
            await asyncio.shield(self._repo.fail_search(search_id))
        except Exception:  # pragma: no cover
            logger.exception("Could not mark search %s as failed", search_id)


def _rates_payload(rates: Optional[ExchangeRates]) -> dict:
    if rates is None:
        return {"currency": None, "rates_available": False, "as_of": None, "source": None}
    return {
        "currency": rates.base,
        "rates_available": True,
        "as_of": rates.as_of.isoformat(),
        "source": rates.source,
    }
