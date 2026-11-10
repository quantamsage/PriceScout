"""Generic adapter for websites without a dedicated adapter.

Search-URL discovery, in order:
  1. a `{query}` placeholder in the submitted URL
     (e.g. https://shop.example/find?term={query});
  2. the homepage's search <form> (GET forms only);
  3. an OpenSearch description (<link rel="search">);
  4. common e-commerce patterns (Shopify, WooCommerce, Magento, ...).

Results are extracted with the shared strategies (JSON-LD, microdata,
generic product cards, Open Graph).
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import quote_plus, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from app.scrapers.base import HtmlAdapter, SearchContext
from app.scrapers.extraction import parse_html
from app.scrapers.fetcher import BlockedError, FetchError, RobotsDisallowedError

logger = logging.getLogger(__name__)

PLACEHOLDERS = ("{query}", "%7Bquery%7D", "%7bquery%7d")
SEARCH_PARAM_NAMES = ("q", "query", "s", "search", "k", "keyword", "keywords", "term", "text", "search_query", "searchTerm")

COMMON_PATTERNS = (
    "/search?q={q}",
    "/search?type=product&q={q}",  # Shopify
    "/?s={q}&post_type=product",  # WooCommerce
    "/catalogsearch/result/?q={q}",  # Magento
    "/search?query={q}",
    "/catalog/?q={q}",
)


class GenericAdapter(HtmlAdapter):
    kind = "generic"

    @property
    def name(self) -> str:
        return "Generic"

    @classmethod
    def matches(cls, host: str) -> bool:
        return True

    def _site_domain(self) -> str:
        return self.host.removeprefix("www.")

    def _is_same_site(self, url: str) -> bool:
        host = (urlsplit(url).hostname or "").lower()
        domain = self._site_domain()
        return host == domain or host.endswith("." + domain)

    async def search_urls(self, query: str, ctx: SearchContext) -> list[str]:
        for placeholder in PLACEHOLDERS:
            if placeholder in self.website_url:
                return [self.website_url.replace(placeholder, quote_plus(query))]

        candidates: list[str] = []
        try:
            homepage = await ctx.fetcher.get(self.website_url)
        except (RobotsDisallowedError, BlockedError):
            raise
        except FetchError as exc:
            logger.info("Homepage fetch failed for %s: %s", self.website_url, exc.user_message)
            homepage = None

        if homepage is not None:
            soup = parse_html(homepage.text)
            form_url = self._url_from_form(soup, homepage.url, query)
            if form_url:
                candidates.append(form_url)
            opensearch_url = await self._url_from_opensearch(soup, homepage.url, query, ctx)
            if opensearch_url:
                candidates.append(opensearch_url)

        q = quote_plus(query)
        candidates.extend(self.origin + pattern.format(q=q) for pattern in COMMON_PATTERNS)

        unique: list[str] = []
        for url in candidates:
            if url not in unique and self._is_same_site(url):
                unique.append(url)
        return unique

    def _url_from_form(self, soup: BeautifulSoup, page_url: str, query: str) -> Optional[str]:
        for form in soup.find_all("form"):
            method = (form.get("method") or "get").lower()
            if method != "get" or form.find("input", attrs={"type": "password"}):
                continue
            search_input = form.find("input", attrs={"type": "search"})
            if search_input is None:
                for name in SEARCH_PARAM_NAMES:
                    search_input = form.find("input", attrs={"name": name})
                    if search_input is not None:
                        break
            if search_input is None or not search_input.get("name"):
                continue
            params: list[tuple[str, str]] = []
            for field in form.find_all("input"):
                field_name = field.get("name")
                if not field_name:
                    continue
                if field is search_input:
                    params.append((field_name, query))
                elif (field.get("type") or "").lower() == "hidden":
                    params.append((field_name, field.get("value") or ""))
            action = urljoin(page_url, form.get("action") or page_url)
            parts = urlsplit(action)
            if parts.scheme not in ("http", "https"):
                continue
            return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", urlencode(params), ""))
        return None

    async def _url_from_opensearch(
        self, soup: BeautifulSoup, page_url: str, query: str, ctx: SearchContext
    ) -> Optional[str]:
        link = soup.find("link", attrs={"rel": "search", "type": re.compile("opensearch", re.I)})
        if link is None or not link.get("href"):
            return None
        description_url = urljoin(page_url, link["href"])
        if not self._is_same_site(description_url):
            return None
        try:
            description = await ctx.fetcher.get(description_url)
        except FetchError:
            return None
        xml = BeautifulSoup(description.text, "xml")
        for url_tag in xml.find_all("Url"):
            if (url_tag.get("type") or "").lower() != "text/html":
                continue
            template = url_tag.get("template") or ""
            if "{searchTerms}" not in template:
                continue
            filled = re.sub(r"\{[a-zA-Z:]+\?\}", "", template.replace("{searchTerms}", quote_plus(query)))
            return urljoin(page_url, filled)
        return None
