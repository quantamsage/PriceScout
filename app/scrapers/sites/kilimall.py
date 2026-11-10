"""Kilimall (Kenya, Uganda, Nigeria).

Kilimall's storefront is a JavaScript application, so its results usually
need the Playwright fallback.
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import quote_plus

from app.scrapers.base import HtmlAdapter, SearchContext
from app.scrapers.extraction import SelectorConfig

_TLD_CURRENCY = {"co.ke": "KES", "co.ug": "UGX", "com.ng": "NGN"}


class KilimallAdapter(HtmlAdapter):
    host_pattern = re.compile(r"(^|\.)kilimall\.(" + "|".join(re.escape(t) for t in _TLD_CURRENCY) + r")$")
    requires_js = True
    selectors = SelectorConfig(
        item=".product-item, .listing-item, .goods-item, [class*='product-card']",
        name=".product-title, .title, .goods-name, [class*='title']",
        link="a[href*='/listing/'], a[href*='/item/'], a[href]",
        price=".product-price, .price, .goods-price, [class*='price']",
        image="img",
    )

    @property
    def name(self) -> str:
        return "Kilimall"

    def default_currency(self) -> Optional[str]:
        tld = self.host.split("kilimall.", 1)[-1]
        return _TLD_CURRENCY.get(tld)

    async def search_urls(self, query: str, ctx: SearchContext) -> list[str]:
        return [f"{self.origin}/search?q={quote_plus(query)}"]
