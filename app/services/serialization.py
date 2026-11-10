"""Convert ORM rows into API/SSE payloads."""

from __future__ import annotations

from typing import Any, Optional

from app.models import ProductListing, Search, SearchWebsite
from app.schemas.search import ListingOut, SearchOut, SummaryOut, WebsiteOut
from app.services.comparison_rules import ComparableListing, compute_comparison


def listing_out(listing: ProductListing) -> ListingOut:
    snapshot = listing.snapshots[-1]
    return ListingOut(
        id=listing.id,
        website_id=listing.search_website_id,
        website=listing.website.display_name if listing.website else "",
        name=listing.name,
        url=listing.url,
        image_url=listing.image_url,
        seller=listing.seller,
        availability=listing.availability,
        price=snapshot.price,
        currency=snapshot.currency,
        raw_price_text=snapshot.raw_price_text,
        converted_price=snapshot.converted_price,
        converted_currency=snapshot.converted_currency,
        exchange_rate=snapshot.exchange_rate,
        exchange_rate_source=snapshot.exchange_rate_source,
        exchange_rate_as_of=snapshot.exchange_rate_as_of,
        retrieved_at=snapshot.retrieved_at,
        match_score=listing.match_score,
        is_match=listing.is_match,
        group_key=listing.group_key,
        extraction_method=listing.extraction_method,
    )


def website_out(website: SearchWebsite) -> WebsiteOut:
    return WebsiteOut(
        id=website.id,
        url=website.url,
        domain=website.domain,
        display_name=website.display_name,
        adapter=website.adapter,
        status=website.status,
        message=website.message,
        product_count=website.product_count,
    )


def comparable(listings: list[ListingOut]) -> list[ComparableListing]:
    return [
        ComparableListing(
            id=l.id,
            price=l.price,
            currency=l.currency,
            converted_price=l.converted_price,
            converted_currency=l.converted_currency,
            is_match=l.is_match,
        )
        for l in listings
    ]


def search_out(search: Search, preferred_currency: Optional[str] = None) -> SearchOut:
    listings = [listing_out(l) for l in sorted(search.listings, key=lambda l: l.id)]
    result = compute_comparison(comparable(listings), preferred_currency or search.comparison_currency)
    return SearchOut(
        id=search.id,
        query=search.query,
        status=search.status,
        created_at=search.created_at,
        websites=[website_out(w) for w in search.websites],
        listings=listings,
        summary=SummaryOut(
            status=search.status,
            total_websites=search.total_websites,
            successful_websites=search.successful_websites,
            failed_websites=search.failed_websites,
            matching_products=search.matching_products,
            comparison_currency=result.currency,
            cheapest_listing_id=result.cheapest_id,
            completed_at=search.completed_at,
        ),
    )


def dump(model: Any) -> Any:
    return model.model_dump(mode="json")
