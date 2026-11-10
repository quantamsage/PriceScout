"""Maps a website URL to the adapter that should search it.

Resolution order: official API adapters (when configured) → site-specific
adapters → generic adapter. To support a new website, write an adapter and
add it to `SITE_ADAPTERS` (or call `register_site_adapter`). Nothing in the
comparison service needs to change.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from app.config import Settings
from app.scrapers.api.ebay_api import EbayApiAdapter
from app.scrapers.base import HtmlAdapter, WebsiteAdapter
from app.scrapers.generic import GenericAdapter
from app.scrapers.sites.amazon import AmazonAdapter
from app.scrapers.sites.ebay import EbayAdapter
from app.scrapers.sites.jumia import JumiaAdapter
from app.scrapers.sites.kilimall import KilimallAdapter

API_ADAPTERS: list[type[EbayApiAdapter]] = [EbayApiAdapter]
SITE_ADAPTERS: list[type[HtmlAdapter]] = [AmazonAdapter, EbayAdapter, JumiaAdapter, KilimallAdapter]


def register_site_adapter(adapter: type[HtmlAdapter]) -> type[HtmlAdapter]:
    """Decorator/helper for registering additional site adapters."""
    if adapter not in SITE_ADAPTERS:
        SITE_ADAPTERS.append(adapter)
    return adapter


def resolve_adapter(website_url: str, settings: Settings) -> WebsiteAdapter:
    host = (urlsplit(website_url).hostname or "").lower()
    for api_adapter in API_ADAPTERS:
        if api_adapter.available(settings) and api_adapter.matches(host):
            return api_adapter(website_url, settings)
    for site_adapter in SITE_ADAPTERS:
        if site_adapter.matches(host):
            return site_adapter(website_url)
    return GenericAdapter(website_url)
