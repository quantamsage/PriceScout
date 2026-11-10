"""Playwright fallback for JavaScript-rendered pages.

The browser is started lazily (only when an adapter actually needs it),
shared across searches, limited by a semaphore, and every page/context is
closed after use. Each request the page makes is checked against the SSRF
rules; images, media and fonts are not loaded and downloads are refused.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional
from urllib.parse import urlsplit

from app.config import Settings
from app.scrapers.fetcher import FetchError
from app.services.url_safety import Resolver, UnsafeURLError, system_resolver, validate_url

logger = logging.getLogger(__name__)

_BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}


class BrowserUnavailableError(FetchError):
    pass


class BrowserPool:
    def __init__(self, settings: Settings, resolver: Resolver = system_resolver) -> None:
        self._settings = settings
        self._resolver = resolver
        self._semaphore = asyncio.Semaphore(settings.playwright_max_concurrency)
        self._start_lock = asyncio.Lock()
        self._playwright: Any = None
        self._browser: Any = None
        self._unavailable_reason: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return self._settings.playwright_enabled and self._unavailable_reason is None

    async def _ensure_browser(self) -> Any:
        if self._browser is not None:
            return self._browser
        async with self._start_lock:
            if self._browser is not None:
                return self._browser
            if self._unavailable_reason:
                raise BrowserUnavailableError(self._unavailable_reason)
            try:
                from playwright.async_api import async_playwright
            except ImportError:
                self._unavailable_reason = "JavaScript rendering is unavailable (Playwright not installed)."
                raise BrowserUnavailableError(self._unavailable_reason) from None
            try:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(headless=True)
            except Exception as exc:  # browser binaries missing, sandbox errors...
                logger.warning("Could not launch Chromium: %s", exc)
                self._unavailable_reason = (
                    "JavaScript rendering is unavailable (run `playwright install chromium`)."
                )
                if self._playwright is not None:
                    await self._playwright.stop()
                    self._playwright = None
                raise BrowserUnavailableError(self._unavailable_reason) from exc
            return self._browser

    async def render(self, url: str) -> tuple[str, str]:
        """Render `url` and return (final_url, html)."""
        if not self._settings.playwright_enabled:
            raise BrowserUnavailableError("JavaScript rendering is disabled.")
        try:
            await validate_url(url, self._resolver, self._settings.allow_private_networks)
        except UnsafeURLError as exc:
            raise FetchError(str(exc)) from exc
        browser = await self._ensure_browser()
        async with self._semaphore:
            context = await browser.new_context(
                user_agent=self._settings.user_agent,
                java_script_enabled=True,
                accept_downloads=False,
                service_workers="block",
            )
            try:
                page = await context.new_page()
                await page.route("**/*", self._guard_route)
                timeout_ms = int(self._settings.playwright_timeout_seconds * 1000)
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 8000))
                    except Exception:
                        pass  # long-polling pages never go idle; use what rendered
                    html = await page.content()
                    final_url = page.url
                except Exception as exc:
                    logger.info("Browser rendering failed for %s: %s", url, exc)
                    raise FetchError("The website could not be rendered in a browser.") from exc
                if len(html.encode("utf-8", errors="ignore")) > self._settings.max_response_bytes:
                    raise FetchError("The website's response was too large.")
                return final_url, html
            finally:
                await context.close()

    async def _guard_route(self, route: Any) -> None:
        request = route.request
        if request.resource_type in _BLOCKED_RESOURCE_TYPES:
            await route.abort()
            return
        scheme = urlsplit(request.url).scheme
        if scheme in ("data", "blob"):
            await route.continue_()
            return
        try:
            await validate_url(request.url, self._resolver, self._settings.allow_private_networks)
        except UnsafeURLError:
            await route.abort()
            return
        await route.continue_()

    async def aclose(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            finally:
                self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            finally:
                self._playwright = None
