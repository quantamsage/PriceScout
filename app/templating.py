"""Shared Jinja2 template configuration (autoescaping is on for .html)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _format_datetime(value: Optional[datetime]) -> str:
    if value is None:
        return "—"
    return value.strftime("%Y-%m-%d %H:%M UTC")


templates.env.filters["datetime"] = _format_datetime
