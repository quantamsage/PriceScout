"""Shared test fixtures. Tests never touch live websites."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}",
        playwright_enabled=False,
        per_host_min_interval_seconds=0,
        request_timeout_seconds=5,
        site_timeout_seconds=10,
        exchange_rates="",
        exchange_rate_provider_url=None,
        ebay_client_id=None,
        ebay_client_secret=None,
        _env_file=None,
    )
