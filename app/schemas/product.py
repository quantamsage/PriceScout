"""The normalized product structure every website adapter returns."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

Availability = Literal["in_stock", "out_of_stock", "unknown"]

_WHITESPACE = re.compile(r"\s+")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(value: Optional[str], max_length: int) -> Optional[str]:
    """Collapse whitespace, drop control characters and truncate."""
    if value is None:
        return None
    value = _CONTROL_CHARS.sub("", str(value))
    value = _WHITESPACE.sub(" ", value).strip()
    if not value:
        return None
    return value[:max_length]


def is_http_url(value: Optional[str]) -> bool:
    if not value:
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)


class Product(BaseModel):
    """A single product listing extracted from a website.

    `price` is None when no price could be extracted — it is never assumed
    to be zero. `currency` is None when the currency could not be determined.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=2, max_length=500)
    url: str
    price: Optional[Decimal] = None
    currency: Optional[str] = None
    raw_price_text: Optional[str] = None
    image_url: Optional[str] = None
    seller: Optional[str] = None
    availability: Availability = "unknown"
    source: str
    extraction_method: str

    @field_validator("name", mode="before")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        cleaned = clean_text(value, 500)
        if not cleaned:
            raise ValueError("product name is empty")
        return cleaned

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        if not is_http_url(value):
            raise ValueError("product URL must be http(s)")
        return value[:2048]

    @field_validator("image_url", mode="before")
    @classmethod
    def _validate_image(cls, value: Optional[str]) -> Optional[str]:
        # Drop data:, javascript: and other non-http(s) image sources.
        return value[:2048] if is_http_url(value) else None

    @field_validator("seller", mode="before")
    @classmethod
    def _clean_seller(cls, value: Optional[str]) -> Optional[str]:
        return clean_text(value, 255)

    @field_validator("raw_price_text", mode="before")
    @classmethod
    def _clean_raw_price(cls, value: Optional[str]) -> Optional[str]:
        return clean_text(value, 100)

    @field_validator("price")
    @classmethod
    def _validate_price(cls, value: Optional[Decimal]) -> Optional[Decimal]:
        if value is None:
            return None
        if not value.is_finite() or value <= 0 or value >= Decimal("1e10"):
            return None
        return value.quantize(Decimal("0.01"))

    @field_validator("currency", mode="before")
    @classmethod
    def _validate_currency(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = str(value).strip().upper()
        return value if re.fullmatch(r"[A-Z]{3}", value) else None
