"""Amazon search results (all major regional storefronts).

Amazon frequently serves CAPTCHAs to automated clients; when that happens
the search is reported as blocked — it is never bypassed.
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import quote_plus

from app.scrapers.base import HtmlAdapter, SearchContext
from app.scrapers.extraction import SelectorConfig

_TLD_CURRENCY = {
    "com": "USD", "ca": "CAD", "com.mx": "MXN", "com.br": "BRL", "co.uk": "GBP",
    "de": "EUR", "fr": "EUR", "it": "EUR", "es": "EUR", "nl": "EUR", "ie": "EUR",
    "com.be": "EUR", "se": "SEK", "pl": "PLN", "com.tr": "TRY", "ae": "AED",
    "sa": "SAR", "eg": "EGP", "in": "INR", "co.jp": "JPY", "com.au": "AUD", "sg": "SGD",
}


class AmazonAdapter(HtmlAdapter):
    host_pattern = re.compile(r"(^|\.)amazon\.(" + "|".join(re.escape(t) for t in _TLD_CURRENCY) + r")$")
    selectors = SelectorConfig(
        item='div[data-component-type="s-search-result"]',
        # Current layout: brand in `h2.a-size-mini`, full title in `h2[aria-label]`.
        name="h2[aria-label], h2.a-text-normal, h2 a span, h2 span, h2",
        brand="h2.a-size-mini",
        link='a.a-link-normal[href*="/dp/"], h2 a, a[href*="/dp/"]',
        price="span.a-price:not(.a-text-price) span.a-offscreen, span.a-price span.a-offscreen",
        image="img.s-image",
        availability="span.a-color-price",
    )

    @property
    def name(self) -> str:
        return "Amazon"

    def default_currency(self) -> Optional[str]:
        tld = self.host.split("amazon.", 1)[-1]
        return _TLD_CURRENCY.get(tld)

    async def search_urls(self, query: str, ctx: SearchContext) -> list[str]:
        return [f"{self.origin}/s?k={quote_plus(query)}"]
