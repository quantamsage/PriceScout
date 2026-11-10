"""Product listings found during a search and their price snapshots.

Listings are never overwritten: every search inserts new listing rows and
new price snapshots, so historical prices are preserved. `canonical_url`
lets later features follow the same listing across searches.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.types import DecimalString, UTCDateTime, utcnow

if TYPE_CHECKING:
    from app.models.search import Search
    from app.models.website import SearchWebsite


class ProductListing(Base):
    __tablename__ = "product_listings"
    __table_args__ = (
        Index("ix_product_listings_search_id", "search_id"),
        Index("ix_product_listings_website_id", "search_website_id"),
        Index("ix_product_listings_canonical_url", "canonical_url"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    search_id: Mapped[int] = mapped_column(
        ForeignKey("searches.id", ondelete="CASCADE"), nullable=False
    )
    search_website_id: Mapped[int] = mapped_column(
        ForeignKey("search_websites.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    image_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    seller: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    availability: Mapped[str] = mapped_column(String(20), default="unknown", nullable=False)
    extraction_method: Mapped[str] = mapped_column(String(50), nullable=False)
    match_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_match: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    group_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    search: Mapped["Search"] = relationship(back_populates="listings")
    website: Mapped["SearchWebsite"] = relationship(back_populates="listings")
    snapshots: Mapped[list["PriceSnapshot"]] = relationship(
        back_populates="listing",
        cascade="all, delete-orphan",
        order_by="PriceSnapshot.retrieved_at",
    )


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"
    __table_args__ = (
        Index("ix_price_snapshots_listing_id", "listing_id"),
        Index("ix_price_snapshots_retrieved_at", "retrieved_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("product_listings.id", ondelete="CASCADE"), nullable=False
    )
    # NULL price means "price unavailable" — never stored as zero.
    price: Mapped[Optional[Decimal]] = mapped_column(DecimalString, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    raw_price_text: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    converted_price: Mapped[Optional[Decimal]] = mapped_column(DecimalString, nullable=True)
    converted_currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    exchange_rate: Mapped[Optional[Decimal]] = mapped_column(DecimalString, nullable=True)
    exchange_rate_source: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    exchange_rate_as_of: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    listing: Mapped["ProductListing"] = relationship(back_populates="snapshots")
