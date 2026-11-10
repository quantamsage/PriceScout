"""Request/response schemas for searches."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    websites: list[str] = Field(min_length=1)


class SearchCreated(BaseModel):
    id: int
    url: str
    events_url: str


class ListingOut(BaseModel):
    id: int
    website_id: int
    website: str
    name: str
    url: str
    image_url: Optional[str]
    seller: Optional[str]
    availability: str
    price: Optional[Decimal]
    currency: Optional[str]
    raw_price_text: Optional[str]
    converted_price: Optional[Decimal]
    converted_currency: Optional[str]
    exchange_rate: Optional[Decimal]
    exchange_rate_source: Optional[str]
    exchange_rate_as_of: Optional[datetime]
    retrieved_at: datetime
    match_score: float
    is_match: bool
    group_key: Optional[str]
    extraction_method: str


class WebsiteOut(BaseModel):
    id: int
    url: str
    domain: str
    display_name: str
    adapter: Optional[str]
    status: str
    message: Optional[str]
    product_count: int


class SummaryOut(BaseModel):
    status: str
    total_websites: int
    successful_websites: int
    failed_websites: int
    matching_products: int
    comparison_currency: Optional[str]
    cheapest_listing_id: Optional[int]
    completed_at: Optional[datetime]


class SearchOut(BaseModel):
    id: int
    query: str
    status: str
    created_at: datetime
    websites: list[WebsiteOut]
    listings: list[ListingOut]
    summary: SummaryOut
