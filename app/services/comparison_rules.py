"""Pure rules for ranking prices. Shared by the live search and history views.

* A listing is comparable only if it has a price *and* that price is
  expressed in the comparison currency (natively or via a real conversion).
* If no exchange rates are configured, the comparison currency is the single
  currency shared by all priced matching listings; with mixed currencies and
  no rates, nothing is ranked (different currencies are never compared
  directly).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class ComparableListing:
    id: int
    price: Optional[Decimal]
    currency: Optional[str]
    converted_price: Optional[Decimal]
    converted_currency: Optional[str]
    is_match: bool


@dataclass(frozen=True)
class ComparisonResult:
    currency: Optional[str]
    values: dict[int, Decimal]  # listing id -> price in `currency`
    cheapest_id: Optional[int]


def comparable_value(listing: ComparableListing, currency: Optional[str]) -> Optional[Decimal]:
    if currency is None or listing.price is None:
        return None
    if listing.currency == currency:
        return listing.price
    if listing.converted_currency == currency and listing.converted_price is not None:
        return listing.converted_price
    return None


def choose_currency(listings: list[ComparableListing], preferred: Optional[str]) -> Optional[str]:
    priced = [l for l in listings if l.price is not None and l.currency]
    if preferred and any(l.converted_currency == preferred for l in priced):
        return preferred
    currencies = {l.currency for l in priced if l.is_match} or {l.currency for l in priced}
    if len(currencies) == 1:
        return next(iter(currencies))
    # Mixed currencies without exchange rates: no overall ranking.
    return None


def compute_comparison(listings: list[ComparableListing], preferred: Optional[str]) -> ComparisonResult:
    currency = choose_currency(listings, preferred)
    values = {
        l.id: value for l in listings if (value := comparable_value(l, currency)) is not None
    }
    matching = [(values[l.id], l.id) for l in listings if l.is_match and l.id in values]
    cheapest = min(matching)[1] if matching else None
    return ComparisonResult(currency=currency, values=values, cheapest_id=cheapest)
