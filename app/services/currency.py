"""Currency conversion.

Rates come from (in order) the explicitly configured static rates or the
explicitly configured provider URL. Nothing is invented: when no rate is
known for a currency, `convert` returns None and the listing is shown in
its original currency only.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from app.config import Settings
from app.scrapers.fetcher import FetchError, SafeFetcher

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExchangeRates:
    base: str
    rates: dict[str, Decimal]  # units of currency per 1 `base`
    as_of: datetime
    source: str


@dataclass(frozen=True)
class Conversion:
    amount: Decimal
    currency: str
    rate: Decimal  # `currency` per 1 unit of the original currency
    as_of: datetime
    source: str


def convert(amount: Optional[Decimal], from_currency: Optional[str], rates: Optional[ExchangeRates]) -> Optional[Conversion]:
    if amount is None or not from_currency or rates is None:
        return None
    from_currency = from_currency.upper()
    if from_currency == rates.base:
        rate = Decimal(1)
    else:
        per_base = rates.rates.get(from_currency)
        if not per_base or per_base <= 0:
            return None
        rate = Decimal(1) / per_base
    return Conversion(
        amount=(amount * rate).quantize(Decimal("0.01")),
        currency=rates.base,
        rate=rate.quantize(Decimal("0.00000001")),
        as_of=rates.as_of,
        source=rates.source,
    )


class CurrencyService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cached: Optional[ExchangeRates] = None
        self._cached_until = 0.0
        self._lock: Optional[asyncio.Lock] = None

    @property
    def base(self) -> str:
        return self._settings.comparison_currency

    def static_rates(self) -> Optional[ExchangeRates]:
        try:
            rates = self._settings.static_exchange_rates()
        except (ValueError, InvalidOperation):
            logger.warning("PRICESCOUT_EXCHANGE_RATES is not valid JSON; ignoring it")
            return None
        if not rates:
            return None
        base = self.base
        # Re-express relative to the comparison currency if needed.
        if base not in rates:
            logger.warning("Static exchange rates do not include %s; ignoring them", base)
            return None
        base_rate = rates[base]
        normalized = {code: value / base_rate for code, value in rates.items()}
        as_of = _parse_datetime(self._settings.exchange_rates_as_of) or datetime.now(timezone.utc)
        return ExchangeRates(base=base, rates=normalized, as_of=as_of, source="configured rates")

    async def get_rates(self, fetcher: SafeFetcher) -> Optional[ExchangeRates]:
        static = self.static_rates()
        if static is not None:
            return static
        if not self._settings.exchange_rate_provider_url:
            return None
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._cached and time.monotonic() < self._cached_until:
                return self._cached
            url = self._settings.exchange_rate_provider_url.replace("{base}", self.base)
            try:
                result = await fetcher.get(url, check_robots=False, headers={"Accept": "application/json"})
                payload = result.json()
                raw_rates = payload.get("rates") or payload.get("conversion_rates") or {}
                rates = {
                    str(code).upper(): Decimal(str(value))
                    for code, value in raw_rates.items()
                    if _positive(value)
                }
                if not rates:
                    raise ValueError("no rates in response")
                provider_base = str(payload.get("base") or payload.get("base_code") or self.base).upper()
                if provider_base != self.base:
                    if self.base not in rates:
                        raise ValueError("provider base mismatch")
                    divisor = rates[self.base]
                    rates = {k: v / divisor for k, v in rates.items()}
                rates[self.base] = Decimal(1)
                as_of = _timestamp(payload) or datetime.now(timezone.utc)
                host = url.split("/")[2] if "//" in url else url
                self._cached = ExchangeRates(self.base, rates, as_of, host)
                self._cached_until = time.monotonic() + self._settings.exchange_rate_cache_seconds
                return self._cached
            except (FetchError, ValueError, InvalidOperation, AttributeError) as exc:
                logger.warning("Exchange-rate provider unavailable: %s", exc)
                return self._cached  # stale-but-real is better than nothing


def _positive(value: object) -> bool:
    try:
        return Decimal(str(value)) > 0
    except (InvalidOperation, ValueError):
        return False


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _timestamp(payload: dict) -> Optional[datetime]:
    for key in ("time_last_update_unix", "timestamp"):
        value = payload.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return datetime.fromtimestamp(value, tz=timezone.utc)
    for key in ("date", "time_last_update_utc"):
        value = payload.get(key)
        if isinstance(value, str):
            parsed = _parse_datetime(value)
            if parsed:
                return parsed
    return None
