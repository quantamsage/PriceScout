"""Owns long-lived search resources: background tasks, the event broker,
the shared Playwright browser pool and currency cache."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional
from urllib.parse import urlsplit

import httpx

from app.config import Settings
from app.models import Search
from app.repositories.search_repository import NewWebsite, SearchRepository
from app.scrapers.browser import BrowserPool
from app.scrapers.fetcher import SafeFetcher
from app.scrapers.registry import resolve_adapter
from app.services.comparison import ComparisonService, WebsiteJob
from app.services.currency import CurrencyService
from app.services.search_events import SearchEventBroker
from app.services.url_safety import Resolver, system_resolver

logger = logging.getLogger(__name__)


class SearchManager:
    def __init__(
        self,
        settings: Settings,
        repository: SearchRepository,
        *,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        resolver: Resolver = system_resolver,
        browser: Optional[BrowserPool] = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.broker = SearchEventBroker()
        self._transport = transport
        self._resolver = resolver
        self.browser = browser if browser is not None else (
            BrowserPool(settings, resolver) if settings.playwright_enabled else None
        )
        self.currency = CurrencyService(settings)
        self._tasks: set[asyncio.Task] = set()
        self._service = ComparisonService(
            settings,
            repository,
            self.broker,
            self._fetcher,
            self.currency,
            self.browser,
        )

    @asynccontextmanager
    async def _fetcher(self) -> AsyncIterator[SafeFetcher]:
        fetcher = SafeFetcher(self.settings, transport=self._transport, resolver=self._resolver)
        try:
            yield fetcher
        finally:
            await fetcher.aclose()

    async def create_search(self, query: str, website_urls: list[str]) -> Search:
        websites = []
        for url in website_urls:
            adapter = resolve_adapter(url, self.settings)
            domain = (urlsplit(url).hostname or url).removeprefix("www.")
            display = domain if adapter.kind == "generic" else f"{adapter.name} ({domain})"
            websites.append(NewWebsite(url=url, domain=domain, display_name=display, adapter=adapter.name))
        search = await self.repository.create_search(query, websites)
        return search

    def start(self, search: Search) -> asyncio.Task:
        jobs = [WebsiteJob(id=w.id, url=w.url, display_name=w.display_name) for w in search.websites]
        # Open the channel before the task runs so early subscribers attach.
        self.broker.open(search.id)
        task = asyncio.create_task(self._service.run(search.id, search.query, jobs), name=f"search-{search.id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def wait_idle(self) -> None:
        """Wait for all running searches (used by tests)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)
        if self.browser is not None:
            await self.browser.aclose()
