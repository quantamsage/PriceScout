"""Websites submitted with a search and the outcome of searching each one."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

from app.models.types import UTCDateTime

if TYPE_CHECKING:
    from app.models.product import ProductListing
    from app.models.search import Search


class WebsiteStatus:
    PENDING = "pending"
    SEARCHING = "searching"
    COMPLETED = "completed"
    FAILED = "failed"


class SearchWebsite(Base):
    __tablename__ = "search_websites"
    __table_args__ = (
        Index("ix_search_websites_search_id", "search_id"),
        Index("ix_search_websites_domain", "domain"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    search_id: Mapped[int] = mapped_column(
        ForeignKey("searches.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    adapter: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=WebsiteStatus.PENDING, nullable=False)
    message: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    product_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)

    search: Mapped["Search"] = relationship(back_populates="websites")
    listings: Mapped[list["ProductListing"]] = relationship(back_populates="website")
