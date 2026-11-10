"""Custom column types."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import DateTime, String
from sqlalchemy.types import TypeDecorator


class DecimalString(TypeDecorator):
    """Stores Decimals losslessly as text (SQLite has no native decimal type)."""

    impl = String(40)
    cache_ok = True

    def process_bind_param(self, value: Optional[Decimal], dialect) -> Optional[str]:  # noqa: ANN001
        if value is None:
            return None
        return format(Decimal(value), "f")

    def process_result_value(self, value: Optional[str], dialect) -> Optional[Decimal]:  # noqa: ANN001
        if value is None:
            return None
        return Decimal(value)


class UTCDateTime(TypeDecorator):
    """Timezone-aware datetimes stored as UTC (SQLite drops tzinfo otherwise)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Optional[datetime], dialect) -> Optional[datetime]:  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: Optional[datetime], dialect) -> Optional[datetime]:  # noqa: ANN001
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
