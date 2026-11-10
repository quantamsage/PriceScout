"""Application settings loaded from environment variables / a `.env` file."""

from __future__ import annotations

import json
from decimal import Decimal
from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PRICESCOUT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "PriceScout"
    debug: bool = False
    log_level: str = "INFO"

    # --- Database -------------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./data/pricescout.db"

    # --- Search limits --------------------------------------------------
    max_websites_per_search: int = Field(default=8, ge=1, le=25)
    max_query_length: int = Field(default=200, ge=10, le=500)
    max_results_per_site: int = Field(default=25, ge=1, le=100)
    max_concurrent_sites: int = Field(default=4, ge=1, le=16)
    site_timeout_seconds: float = Field(default=45.0, gt=0)

    # --- HTTP fetching --------------------------------------------------
    request_timeout_seconds: float = Field(default=15.0, gt=0)
    max_response_bytes: int = Field(default=5 * 1024 * 1024, ge=10_000)
    max_redirects: int = Field(default=5, ge=0, le=10)
    per_host_min_interval_seconds: float = Field(default=1.0, ge=0)
    max_search_url_attempts: int = Field(default=3, ge=1, le=6)
    respect_robots_txt: bool = True
    # Identifies the tool honestly; we do not impersonate browsers to evade
    # bot protection.
    user_agent: str = "Mozilla/5.0 (compatible; PriceScout/1.0; personal price comparison)"
    # Only for tests / local development: allow private network targets.
    allow_private_networks: bool = False

    # --- Playwright fallback --------------------------------------------
    playwright_enabled: bool = True
    playwright_max_concurrency: int = Field(default=2, ge=1, le=8)
    playwright_timeout_seconds: float = Field(default=25.0, gt=0)

    # --- Matching -------------------------------------------------------
    match_threshold: float = Field(default=0.6, ge=0, le=1)

    # --- Currency -------------------------------------------------------
    comparison_currency: str = "USD"
    # Static rates: JSON object of "units of currency per 1 comparison currency",
    # e.g. {"USD": 1, "KES": 129.2, "EUR": 0.92}. Leave empty to disable.
    exchange_rates: str = ""
    exchange_rates_as_of: Optional[str] = None
    # Optional provider returning {"rates": {...}} relative to a base currency.
    # `{base}` is replaced by the comparison currency, e.g.
    # https://open.er-api.com/v6/latest/{base}
    exchange_rate_provider_url: Optional[str] = None
    exchange_rate_cache_seconds: int = Field(default=3600, ge=60)

    # --- Official APIs (optional) ---------------------------------------
    ebay_client_id: Optional[str] = None
    ebay_client_secret: Optional[str] = None
    ebay_marketplace_id: str = "EBAY_US"

    @field_validator("comparison_currency")
    @classmethod
    def _upper_currency(cls, value: str) -> str:
        value = value.strip().upper()
        if len(value) != 3 or not value.isalpha():
            raise ValueError("comparison_currency must be a 3-letter ISO code")
        return value

    def static_exchange_rates(self) -> dict[str, Decimal]:
        """Parse the configured static rates (empty dict if not configured)."""
        if not self.exchange_rates.strip():
            return {}
        raw = json.loads(self.exchange_rates)
        rates: dict[str, Decimal] = {}
        for code, value in raw.items():
            rate = Decimal(str(value))
            if rate > 0:
                rates[str(code).upper()] = rate
        return rates

    @property
    def ebay_api_configured(self) -> bool:
        return bool(self.ebay_client_id and self.ebay_client_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
