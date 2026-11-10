"""Common adapter interface.

Every adapter — official API, site-specific or generic — implements
`WebsiteAdapter.search()` and returns a list of normalized `Product`s. The
comparison service only ever talks to this interface.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Optional
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from app.config import Settings
from app.schemas.product import Product
from app.scrapers.browser import BrowserPool, BrowserUnavailableError
from app.scrapers.extraction import (
    SelectorConfig,
    dedupe,
    extract_generic_cards,
    extract_json_ld,
    extract_microdata,
    extract_open_graph,
    extract_with_selectors,
    looks_blocked,
    looks_js_rendered,
    parse_html,
)
from app.scrapers.fetcher import BlockedError, FetchError, RobotsDisallowedError, SafeFetcher

logger = logging.getLogger(__name__)


class UnsupportedWebsiteError(FetchError):
    """The website cannot be searched automatically."""


@dataclass
class SearchContext:
    settings: Settings
    fetcher: SafeFetcher
    browser: Optional[BrowserPool] = None


class WebsiteAdapter(ABC):
    """Base class for all adapters."""

    kind: ClassVar[str] = "site"  # "api" | "site" | "generic"
    host_pattern: ClassVar[Optional[re.Pattern[str]]] = None

    def __init__(self, website_url: str) -> None:
        parts = urlsplit(website_url)
        self.website_url = website_url
        self.scheme = parts.scheme or "https"
        self.host = (parts.hostname or "").lower()
        self.origin = f"{self.scheme}://{parts.netloc}"

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable adapter name, e.g. 'Amazon'."""

    @abstractmethod
    async def search(self, query: str, ctx: SearchContext) -> list[Product]:
        """Search the website for `query` and return normalized products."""

    @classmethod
    def matches(cls, host: str) -> bool:
        return bool(cls.host_pattern and cls.host_pattern.search(host.lower()))

    @property
    def source(self) -> str:
        return self.host.removeprefix("www.")


class HtmlAdapter(WebsiteAdapter):
    """Shared flow for adapters that scrape HTML search-result pages:

    HTTPX + BeautifulSoup first; Playwright only if the page needs
    JavaScript and nothing could be extracted from the static HTML.
    """

    selectors: ClassVar[Optional[SelectorConfig]] = None
    requires_js: ClassVar[bool] = False
    use_generic_fallback: ClassVar[bool] = True

    def default_currency(self) -> Optional[str]:
        """Currency the site is known to use (site-specific adapters only)."""
        return None

    @abstractmethod
    async def search_urls(self, query: str, ctx: SearchContext) -> list[str]:
        """Candidate search-result URLs, most likely first."""

    def extract(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        currency = self.default_currency()
        strategies = [lambda: extract_json_ld(soup, page_url, self.source),
                      lambda: extract_microdata(soup, page_url, self.source)]
        if self.selectors is not None:
            strategies.append(
                lambda: extract_with_selectors(soup, page_url, self.source, self.selectors, currency)
            )
        if self.use_generic_fallback:
            strategies.append(lambda: extract_generic_cards(soup, page_url, self.source, currency))
        strategies.append(lambda: extract_open_graph(soup, page_url, self.source))

        results: list[list[Product]] = []
        for strategy in strategies:
            try:
                products = strategy()
            except Exception:  # malformed markup must not crash a search
                logger.exception("Extraction strategy failed for %s", page_url)
                continue
            if currency:
                products = [p if p.currency else p.model_copy(update={"currency": currency}) for p in products]
            priced = sum(1 for p in products if p.price is not None)
            if priced >= 2:
                return products
            results.append(products)
        # Nothing clearly good: prefer the result with the most priced items.
        results.sort(key=lambda ps: (sum(1 for p in ps if p.price is not None), len(ps)), reverse=True)
        return results[0] if results else []

    async def search(self, query: str, ctx: SearchContext) -> list[Product]:
        limit = ctx.settings.max_results_per_site
        urls = (await self.search_urls(query, ctx))[: ctx.settings.max_search_url_attempts]
        if not urls:
            raise UnsupportedWebsiteError(
                "This website cannot be searched automatically (no search page found)."
            )
        last_error: Optional[FetchError] = None
        needs_js = False
        for url in urls:
            try:
                result = await ctx.fetcher.get(url)
            except (RobotsDisallowedError, BlockedError):
                raise
            except FetchError as exc:
                last_error = exc
                continue
            soup = parse_html(result.text)
            products = self.extract(soup, result.url)
            if not products and (self.requires_js or looks_js_rendered(soup)):
                needs_js = True
                products = await self._render_and_extract(url, ctx)
            if products:
                return dedupe(products, limit)
            if looks_blocked(soup, result.text):
                raise BlockedError("Request blocked by the website (CAPTCHA or bot protection).")
            last_error = None  # page loaded fine, just had no results
        if needs_js and (ctx.browser is None or not ctx.browser.enabled):
            raise FetchError(
                "This website needs JavaScript to show results, and browser rendering is unavailable."
            )
        if last_error is not None:
            raise last_error
        return []

    async def _render_and_extract(self, url: str, ctx: SearchContext) -> list[Product]:
        if ctx.browser is None or not ctx.browser.enabled:
            return []
        try:
            final_url, html = await ctx.browser.render(url)
        except BrowserUnavailableError:
            return []
        soup = parse_html(html)
        products = self.extract(soup, final_url)
        if not products and looks_blocked(soup, html):
            raise BlockedError("Request blocked by the website (CAPTCHA or bot protection).")
        return [p.model_copy(update={"extraction_method": p.extraction_method + "+browser"}) for p in products]
