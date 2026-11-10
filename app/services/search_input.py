"""Validation of what the user submits on the search form / API."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.config import Settings
from app.schemas.product import clean_text
from app.services.url_safety import UnsafeURLError, normalize_website_url


@dataclass
class ValidatedSearch:
    query: str = ""
    websites: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_search_input(query: str, websites: list[str], settings: Settings) -> ValidatedSearch:
    result = ValidatedSearch()
    cleaned_query = clean_text(query, 10_000) or ""
    if not cleaned_query:
        result.errors.append("Enter the product you want to compare.")
    elif len(cleaned_query) > settings.max_query_length:
        result.errors.append(f"The product name must be at most {settings.max_query_length} characters.")
    else:
        result.query = cleaned_query

    submitted = [w.strip() for w in websites if w and w.strip()]
    if not submitted:
        result.errors.append("Add at least one website to search.")
    if len(submitted) > settings.max_websites_per_search:
        result.errors.append(f"You can search up to {settings.max_websites_per_search} websites at once.")
        submitted = submitted[: settings.max_websites_per_search]

    seen_hosts: set[str] = set()
    for raw in submitted:
        try:
            url = normalize_website_url(raw)
        except UnsafeURLError as exc:
            shown = raw if len(raw) <= 80 else raw[:77] + "..."
            result.errors.append(f"“{shown}”: {exc}")
            continue
        key = (urlsplit(url).hostname or "") + urlsplit(url).path.rstrip("/")
        if key in seen_hosts:
            continue  # silently drop duplicates
        seen_hosts.add(key)
        result.websites.append(url)
    return result
