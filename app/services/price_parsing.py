"""Deterministic price and currency extraction from free text.

Examples handled:
    "$1,299.99"          -> (1299.99, USD)
    "KSh 12,999"         -> (12999, KES)
    "1.299,00 €"         -> (1299.00, EUR)
    "£ 849"              -> (849, GBP)
    "US $25.00 to $40"   -> (25.00, USD)   (ranges: lowest price)
    "Call for price"     -> (None, None)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional

# Ordered longest-first so "US$" wins over "$" and "KSh" over "Sh".
CURRENCY_TOKENS: list[tuple[str, str]] = [
    ("US$", "USD"),
    ("USD", "USD"),
    ("KSHS", "KES"),
    ("KSH", "KES"),
    ("KES", "KES"),
    ("EUR", "EUR"),
    ("GBP", "GBP"),
    ("NGN", "NGN"),
    ("UGX", "UGX"),
    ("TZS", "TZS"),
    ("ZAR", "ZAR"),
    ("EGP", "EGP"),
    ("CAD", "CAD"),
    ("C$", "CAD"),
    ("AUD", "AUD"),
    ("A$", "AUD"),
    ("INR", "INR"),
    ("JPY", "JPY"),
    ("CNY", "CNY"),
    ("€", "EUR"),
    ("£", "GBP"),
    ("₦", "NGN"),
    ("₹", "INR"),
    ("¥", "JPY"),
    ("$", "USD"),
]

KNOWN_CURRENCIES = {code for _, code in CURRENCY_TOKENS}

_TOKEN_PATTERN = re.compile(
    "|".join(
        (re.escape(token) if not token.isalpha() else r"(?<![A-Za-z])" + token + r"(?![A-Za-z])")
        for token, _ in CURRENCY_TOKENS
    ),
    re.IGNORECASE,
)

# A number with optional thousands separators (comma, dot, space, NBSP,
# apostrophe) and optional decimal part.
_NUMBER_PATTERN = re.compile(r"\d{1,3}(?:[ ,.  ']\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?")

MAX_PRICE = Decimal("1000000000")


@dataclass(frozen=True)
class ParsedPrice:
    amount: Optional[Decimal]
    currency: Optional[str]


def detect_currency(text: Optional[str]) -> Optional[str]:
    """Return the ISO code of the first currency marker found in `text`."""
    if not text:
        return None
    match = _TOKEN_PATTERN.search(text)
    if not match:
        return None
    token = match.group(0).upper()
    for candidate, code in CURRENCY_TOKENS:
        if candidate.upper() == token:
            return code
    return None


def parse_amount(number: str, currency: Optional[str] = None) -> Optional[Decimal]:
    """Convert a localized number string into a Decimal."""
    cleaned = number.replace(" ", " ").replace(" ", " ").replace("'", " ").strip()
    if not cleaned:
        return None
    last_comma = cleaned.rfind(",")
    last_dot = cleaned.rfind(".")

    if last_comma != -1 and last_dot != -1:
        # Both present: whichever comes last is the decimal separator.
        if last_comma > last_dot:
            cleaned = cleaned.replace(".", "").replace(" ", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "").replace(" ", "")
    elif last_comma != -1:
        decimals = len(cleaned) - last_comma - 1
        if cleaned.count(",") == 1 and decimals in (1, 2):
            cleaned = cleaned.replace(" ", "").replace(",", ".")  # "12,99"
        else:
            cleaned = cleaned.replace(",", "").replace(" ", "")  # "12,999"
    elif last_dot != -1:
        decimals = len(cleaned) - last_dot - 1
        if cleaned.count(".") > 1 or (decimals == 3 and currency == "EUR"):
            cleaned = cleaned.replace(".", "").replace(" ", "")  # "1.299.000"
        else:
            cleaned = cleaned.replace(" ", "")
    else:
        cleaned = cleaned.replace(" ", "")

    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount <= 0 or amount >= MAX_PRICE:
        return None
    return amount


def parse_price(text: Optional[str], default_currency: Optional[str] = None) -> ParsedPrice:
    """Extract the (lowest) price and its currency from `text`.

    `default_currency` is only used when the text has no currency marker and
    the caller *knows* the site's currency (e.g. a site-specific adapter).
    """
    if text is None:
        return ParsedPrice(None, None)
    text = str(text).strip()
    if not text or len(text) > 200:
        return ParsedPrice(None, None)

    currency = detect_currency(text) or (default_currency.upper() if default_currency else None)
    token_spans = [m.span() for m in _TOKEN_PATTERN.finditer(text)]
    near_token: list[Decimal] = []
    anywhere: list[Decimal] = []
    for match in _NUMBER_PATTERN.finditer(text):
        amount = parse_amount(match.group(0), currency)
        if amount is None:
            continue
        anywhere.append(amount)
        start, end = match.span()
        if any(abs(start - t_end) <= 2 or abs(t_start - end) <= 2 for t_start, t_end in token_spans):
            near_token.append(amount)
    # Prefer numbers attached to a currency marker so that "$999 (-20%)" or
    # "2 for $30" are not misread.
    candidates = near_token or anywhere
    if not candidates:
        return ParsedPrice(None, currency)
    # For ranges ("$10 - $20", "from 10 to 20") use the lowest price.
    return ParsedPrice(min(candidates), currency)


def parse_structured_price(value: object, currency: Optional[str]) -> ParsedPrice:
    """Parse a machine-readable price (e.g. JSON-LD "price": "1299.99")."""
    if value is None:
        return ParsedPrice(None, _clean_code(currency))
    if isinstance(value, (int, float, Decimal)):
        try:
            amount = Decimal(str(value))
        except InvalidOperation:
            return ParsedPrice(None, _clean_code(currency))
        if not amount.is_finite() or amount <= 0 or amount >= MAX_PRICE:
            amount = None
        return ParsedPrice(amount, _clean_code(currency))
    text = str(value).strip()
    # Structured data normally uses "." as decimal separator with no grouping.
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return parse_structured_price(Decimal(text), currency)
    parsed = parse_price(text, default_currency=_clean_code(currency))
    return ParsedPrice(parsed.amount, _clean_code(currency) or parsed.currency)


def _clean_code(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    code = str(code).strip().upper()
    return code if re.fullmatch(r"[A-Z]{3}", code) else None
