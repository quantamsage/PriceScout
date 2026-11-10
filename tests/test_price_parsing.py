from decimal import Decimal

import pytest

from app.services.price_parsing import detect_currency, parse_price, parse_structured_price


@pytest.mark.parametrize(
    "text, amount, currency",
    [
        ("$1,299.99", "1299.99", "USD"),
        ("US $849.00", "849.00", "USD"),
        ("KSh 154,999", "154999", "KES"),
        ("Ksh. 1,250", "1250", "KES"),
        ("KES 12 999", "12999", "KES"),
        ("1.299,00 €", "1299.00", "EUR"),
        ("€ 99,95", "99.95", "EUR"),
        ("1.299 €", "1299", "EUR"),
        ("£849", "849", "GBP"),
        ("GBP 12.50", "12.50", "GBP"),
        ("$720.50 to $790.00", "720.50", "USD"),
        ("$999 (-20%)", "999", "USD"),
        ("₦ 1,250,000", "1250000", "NGN"),
    ],
)
def test_parse_price(text, amount, currency):
    parsed = parse_price(text)
    assert parsed.amount == Decimal(amount)
    assert parsed.currency == currency


@pytest.mark.parametrize("text", ["Call for price", "", "Out of stock", "$0.00", "Free"])
def test_missing_or_zero_price_is_unavailable(text):
    assert parse_price(text).amount is None


def test_default_currency_only_used_without_marker():
    assert parse_price("12,999", default_currency="KES").currency == "KES"
    assert parse_price("$12", default_currency="KES").currency == "USD"


def test_no_currency_is_not_guessed():
    parsed = parse_price("1,299.00")
    assert parsed.amount == Decimal("1299.00")
    assert parsed.currency is None


def test_detect_currency_word_boundaries():
    assert detect_currency("Price in KES") == "KES"
    assert detect_currency("EUROPEAN style") is None
    assert detect_currency("no currency here") is None


def test_structured_price():
    assert parse_structured_price("1099.99", "usd").amount == Decimal("1099.99")
    assert parse_structured_price("1099.99", "usd").currency == "USD"
    assert parse_structured_price(0, "USD").amount is None
    assert parse_structured_price(None, "USD").amount is None
    assert parse_structured_price("KSh 2,500", None).currency == "KES"
