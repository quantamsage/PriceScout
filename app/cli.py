"""Command-line helpers.

    python -m app.cli init-db     Create the SQLite database and tables.
"""

from __future__ import annotations

import argparse
import asyncio

from app.config import get_settings
from app.database import create_engine, init_db


async def _init_db() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    try:
        await init_db(engine)
    finally:
        await engine.dispose()
    print(f"Database ready: {settings.database_url}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="create the database schema")
    args = parser.parse_args()
    if args.command == "init-db":
        asyncio.run(_init_db())


if __name__ == "__main__":
    main()
