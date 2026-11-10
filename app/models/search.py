"""Search model: one row per comparison request."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.types import UTCDateTime, utcnow

if TYPE_CHECKING:
    from app.models.product import ProductListing
    from app.models.website import SearchWebsite


class SearchStatus:
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"  # finished, but some websites failed
    FAILED = "failed"  # every website failed


class Search(Base):
    __tablename__ = "searches"
    __table_args__ = (Index("ix_searches_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    query: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=SearchStatus.PENDING, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    comparison_currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    total_websites: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    successful_websites: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_websites: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    matching_products: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    websites: Mapped[list["SearchWebsite"]] = relationship(
        back_populates="search",
        cascade="all, delete-orphan",
        order_by="SearchWebsite.position",
    )
    listings: Mapped[list["ProductListing"]] = relationship(
        back_populates="search",
        cascade="all, delete-orphan",
    )
