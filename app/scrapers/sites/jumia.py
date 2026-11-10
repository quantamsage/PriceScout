"""Jumia (Kenya, Nigeria, Egypt, Uganda, Ghana, Morocco, ...)."""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import quote_plus

from app.scrapers.base import HtmlAdapter, SearchContext
from app.scrapers.extraction import SelectorConfig

_TLD_CURRENCY = {
    "co.ke": "KES", "com.ng": "NGN", "com.eg": "EGP", "ug": "UGX", "co.ug": "UGX",
    "com.gh": "GHS", "ma": "MAD", "ci": "XOF", "sn": "XOF", "dz": "DZD", "co.tz": "TZS",
}


class JumiaAdapter(HtmlAdapter):
    host_pattern = re.compile(r"(^|\.)jumia\.(" + "|".join(re.escape(t) for t in _TLD_CURRENCY) + r")$")
    selectors = SelectorConfig(
        item="article.prd",
        name="h3.name, .name",
        link="a.core, a[href]",
        price="div.prc, .prc",
        image="img.img, img",
        availability=".bdg._oos, .-oos",
    )

    @property
    def name(self) -> str:
        return "Jumia"

    def default_currency(self) -> Optional[str]:
        tld = self.host.split("jumia.", 1)[-1]
        return _TLD_CURRENCY.get(tld)

    async def search_urls(self, query: str, ctx: SearchContext) -> list[str]:
        return [f"{self.origin}/catalog/?q={quote_plus(query)}"]
