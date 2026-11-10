"""Currency conversion and comparison rules."""

import json
from decimal import Decimal

import httpx
import pytest

from app.scrapers.fetcher import SafeFetcher
from app.services.comparison_rules import ComparableListing, compute_comparison
from app.services.currency import CurrencyService, convert
from tests.conftest import make_resolver


def listing(id, price, currency, converted=None, converted_currency=None, is_match=True):
    return ComparableListing(
        id=id,
        price=Decimal(price) if price is not None else None,
        currency=currency,
        converted_price=Decimal(converted) if converted is not None else None,
        converted_currency=converted_currency,
        is_match=is_match,
    )


def test_single_currency_ranks_without_rates():
    result = compute_comparison(
        [listing(1, "154999", "KES"), listing(2, "149500", "KES"), listing(3, None, None)], "USD"
    )
    assert result.currency == "KES"
    assert result.cheapest_id == 2
    assert 3 not in result.values  # unavailable price is never treated as 0


def test_mixed_currencies_without_rates_are_not_ranked():
    result = compute_comparison([listing(1, "1099.99", "USD"), listing(2, "149500", "KES")], "USD")
    assert result.currency is None
    assert result.cheapest_id is None
    assert result.values == {}


def test_converted_prices_are_compared_in_base_currency():
    result = compute_comparison(
        [
            listing(1, "1099.99", "USD"),
            listing(2, "149500", "KES", converted="1157.12", converted_currency="USD"),
            listing(3, "120000", "KES", converted="928.79", converted_currency="USD"),
        ],
        "USD",
    )
    assert result.currency == "USD"
    assert result.cheapest_id == 3


def test_non_matching_listings_are_never_cheapest():
    result = compute_comparison([listing(1, "5", "USD", is_match=False), listing(2, "900", "USD")], "USD")
    assert result.cheapest_id == 2


def test_listing_without_rate_is_excluded_from_ranking():
    result = compute_comparison(
        [listing(1, "1000", "USD"), listing(2, "100", "GBP"), listing(3, "900", "KES", "6.97", "USD")], "USD"
    )
    assert result.currency == "USD"
    assert 2 not in result.values
    assert result.cheapest_id == 3


# --- currency service ----------------------------------------------------------

def test_static_rates_conversion(settings):
    configured = settings.model_copy(
        update={"exchange_rates": json.dumps({"USD": 1, "KES": 129.2, "EUR": 0.92}), "exchange_rates_as_of": "2026-11-01T00:00:00Z"}
    )
    rates = CurrencyService(configured).static_rates()
    assert rates.base == "USD"
    conversion = convert(Decimal("1292"), "KES", rates)
    assert conversion.amount == Decimal("10.00")
    assert conversion.currency == "USD"
    assert conversion.as_of.year == 2026
    assert conversion.source == "configured rates"
    assert convert(Decimal("10"), "USD", rates).amount == Decimal("10.00")


def test_unknown_currency_is_not_converted(settings):
    configured = settings.model_copy(update={"exchange_rates": json.dumps({"USD": 1, "KES": 129.2})})
    rates = CurrencyService(configured).static_rates()
    assert convert(Decimal("10"), "GBP", rates) is None
    assert convert(None, "KES", rates) is None
    assert convert(Decimal("10"), None, rates) is None


def test_no_rates_configured(settings):
    assert CurrencyService(settings).static_rates() is None
    assert convert(Decimal("10"), "KES", None) is None


async def test_rates_from_provider(settings):
    configured = settings.model_copy(update={"exchange_rate_provider_url": "https://rates.example.com/latest/{base}"})

    def handler(request):
        assert str(request.url) == "https://rates.example.com/latest/USD"
        return httpx.Response(
            200,
            json={"base_code": "USD", "time_last_update_unix": 1793404800, "rates": {"USD": 1, "KES": 129.5, "EUR": 0.9}},
        )

    service = CurrencyService(configured)
    async with SafeFetcher(configured, transport=httpx.MockTransport(handler), resolver=make_resolver()) as fetcher:
        rates = await service.get_rates(fetcher)
        assert rates.rates["KES"] == Decimal("129.5")
        assert rates.source == "rates.example.com"
        assert await service.get_rates(fetcher) is rates  # cached


async def test_provider_failure_returns_none(settings):
    configured = settings.model_copy(update={"exchange_rate_provider_url": "https://rates.example.com/latest/{base}"})
    transport = httpx.MockTransport(lambda r: httpx.Response(500, text="down"))
    async with SafeFetcher(configured, transport=transport, resolver=make_resolver()) as fetcher:
        assert await CurrencyService(configured).get_rates(fetcher) is None


def test_invalid_static_rates_are_ignored(settings):
    configured = settings.model_copy(update={"exchange_rates": "{not json"})
    assert CurrencyService(configured).static_rates() is None
