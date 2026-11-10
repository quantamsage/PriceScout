"""Official eBay Browse API adapter (used when credentials are configured).

Docs: https://developer.ebay.com/api-docs/buy/browse/resources/item_summary/methods/search
Uses the OAuth client-credentials flow; tokens are cached until expiry.
"""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Optional

from app.config import Settings
from app.schemas.product import Product
from app.scrapers.base import SearchContext, WebsiteAdapter
from app.scrapers.extraction import make_product, normalize_availability
from app.scrapers.fetcher import BlockedError, FetchError
from app.scrapers.sites.ebay import EbayAdapter
from app.services.price_parsing import parse_structured_price

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
SCOPE = "https://api.ebay.com/oauth/api_scope"

_token_cache: dict[str, tuple[str, float]] = {}
_token_lock = asyncio.Lock()


class EbayApiAdapter(WebsiteAdapter):
    kind = "api"
    host_pattern = EbayAdapter.host_pattern

    def __init__(self, website_url: str, settings: Settings) -> None:
        super().__init__(website_url)
        self._settings = settings

    @classmethod
    def available(cls, settings: Settings) -> bool:
        return settings.ebay_api_configured

    @property
    def name(self) -> str:
        return "eBay API"

    async def _token(self, ctx: SearchContext) -> str:
        client_id = self._settings.ebay_client_id or ""
        async with _token_lock:
            cached = _token_cache.get(client_id)
            if cached and cached[1] > time.monotonic() + 60:
                return cached[0]
            credentials = f"{client_id}:{self._settings.ebay_client_secret}".encode()
            result = await ctx.fetcher.post(
                TOKEN_URL,
                data={"grant_type": "client_credentials", "scope": SCOPE},
                headers={
                    "Authorization": "Basic " + base64.b64encode(credentials).decode(),
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
            )
            payload = result.json()
            token = payload.get("access_token")
            if not token:
                raise FetchError("The eBay API rejected the configured credentials.")
            _token_cache[client_id] = (token, time.monotonic() + float(payload.get("expires_in", 3600)))
            return token

    async def search(self, query: str, ctx: SearchContext) -> list[Product]:
        try:
            token = await self._token(ctx)
        except BlockedError as exc:
            raise FetchError("The eBay API rejected the configured credentials.") from exc
        result = await ctx.fetcher.get(
            SEARCH_URL,
            params={"q": query, "limit": str(ctx.settings.max_results_per_site)},
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": self._settings.ebay_marketplace_id,
                "Accept": "application/json",
            },
            check_robots=False,  # official API, governed by its own terms
        )
        try:
            payload = result.json()
        except ValueError as exc:
            raise FetchError("The eBay API returned an unexpected response.") from exc

        products: list[Product] = []
        for item in payload.get("itemSummaries", []) or []:
            price_node = item.get("price") or {}
            price = parse_structured_price(price_node.get("value"), price_node.get("currency"))
            image: Optional[str] = (item.get("image") or {}).get("imageUrl")
            seller = (item.get("seller") or {}).get("username")
            availability = "unknown"
            availabilities = item.get("estimatedAvailabilities") or []
            if availabilities:
                availability = normalize_availability(availabilities[0].get("estimatedAvailabilityStatus"))
            product = make_product(
                name=item.get("title"),
                url=item.get("itemWebUrl") or item.get("itemHref"),
                price=price.amount,
                currency=price.currency,
                raw_price_text=f"{price_node.get('value')} {price_node.get('currency')}" if price_node else None,
                image_url=image,
                seller=seller,
                availability=availability,
                source=self.source,
                extraction_method="official-api",
            )
            if product:
                products.append(product)
        return products
