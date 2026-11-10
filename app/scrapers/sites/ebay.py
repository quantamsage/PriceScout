"""eBay search results (HTML). See `app/scrapers/api/ebay_api.py` for the
official Browse API adapter, which is preferred when credentials are set."""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import quote_plus

from app.scrapers.base import HtmlAdapter, SearchContext
from app.scrapers.extraction import SelectorConfig

_TLD_CURRENCY = {
    "com": "USD", "co.uk": "GBP", "de": "EUR", "fr": "EUR", "it": "EUR", "es": "EUR",
    "nl": "EUR", "ie": "EUR", "at": "EUR", "be": "EUR", "ca": "CAD", "com.au": "AUD",
    "ch": "CHF", "pl": "PLN",
}


class EbayAdapter(HtmlAdapter):
    host_pattern = re.compile(r"(^|\.)ebay\.(" + "|".join(re.escape(t) for t in _TLD_CURRENCY) + r")$")
    # eBay serves two result layouts (legacy `s-item` and newer `s-card`).
    selectors = SelectorConfig(
        item="li.s-item, li.s-card, div.s-card",
        name=".s-item__title span[role=heading], .s-item__title, .s-card__title span, .s-card__title",
        link="a.s-item__link, a.su-link, a[href*='/itm/']",
        price=".s-item__price, .s-card__price",
        image=".s-item__image-img img, img.s-item__image-img, img.s-card__image, img",
        seller=".s-item__seller-info-text, .s-card__seller-info, .su-card-container__attributes__secondary",
        availability=".s-item__availability, .s-card__availability",
        skip_if_name_matches=r"^shop on ebay$",
    )

    @property
    def name(self) -> str:
        return "eBay"

    def default_currency(self) -> Optional[str]:
        tld = self.host.split("ebay.", 1)[-1]
        return _TLD_CURRENCY.get(tld)

    async def search_urls(self, query: str, ctx: SearchContext) -> list[str]:
        return [f"{self.origin}/sch/i.html?_nkw={quote_plus(query)}"]
