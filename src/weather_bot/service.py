"""Async forecast service for the interactive bot (design section 8).

``get_forecast(slug, title, mode)`` returns ready-to-send HTML text for one of
the modes ``today`` / ``tomorrow`` / ``week`` / ``10days``.

* Pages are fetched with the sync fetcher inside ``asyncio.to_thread``.
* Raw pages and parsed models are cached under ``(url, kyiv_date)`` for
  ``CACHE_TTL_SEC`` seconds (default 600). Today and week share one page URL,
  so one download serves both; tomorrow reuses the cached today page to find
  its tab link.
* Concurrent requests for the same key share one fetch (per-key ``asyncio.Lock``).
* A global ``asyncio.Semaphore(2)`` caps outgoing requests to sinoptik.ua.
* Errors are remembered for 60 s (negative cache) and re-raised immediately.

The model is formatted per request because the title belongs to the user.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from . import fetcher
from .formatter import format_message, format_multiday
from .multiday import parse_day_tabs
from .parser import parse_forecast_day

MODES = ("today", "tomorrow", "week", "10days")
DEFAULT_TTL_SEC = 600.0
NEGATIVE_TTL_SEC = 60.0
MAX_CONCURRENT_FETCHES = 2
KYIV = ZoneInfo("Europe/Kyiv")

# Minimum day tabs per multi-day view (design section 7).
_MIN_DAYS = {"week": 6, "10days": 9}

FetchPage = Callable[[str], str]


def _default_ttl() -> float:
    try:
        return float(os.environ.get("CACHE_TTL_SEC", DEFAULT_TTL_SEC))
    except ValueError:
        return DEFAULT_TTL_SEC


def _kyiv_today() -> date:
    return datetime.now(KYIV).date()


@dataclass
class _Entry:
    value: Any
    stored_at: float
    error: BaseException | None = None


class ForecastService:
    """Forecast cache + fetch coordinator. One instance per process."""

    def __init__(
        self,
        *,
        fetch_page: FetchPage | None = None,
        ttl: float | None = None,
        negative_ttl: float = NEGATIVE_TTL_SEC,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], date] = _kyiv_today,
        max_concurrent: int = MAX_CONCURRENT_FETCHES,
    ) -> None:
        # fetch_page(path) -> html; path is always "/pohoda/...".
        self._fetch_page = fetch_page or fetcher.fetch_html
        self._ttl = _default_ttl() if ttl is None else ttl
        self._negative_ttl = negative_ttl
        self._clock = clock
        self._today = today
        self._max_concurrent = max_concurrent
        self._cache: dict[tuple[str, ...], _Entry] = {}
        self._locks: dict[tuple[str, ...], asyncio.Lock] = {}
        # Created lazily so it binds to the running loop.
        self._semaphore: asyncio.Semaphore | None = None

    def clear(self) -> None:
        self._cache.clear()
        self._locks.clear()

    # -- cache primitives -------------------------------------------------

    def _lookup(self, key: tuple[str, ...]) -> _Entry | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        ttl = self._negative_ttl if entry.error is not None else self._ttl
        if self._clock() - entry.stored_at >= ttl:
            del self._cache[key]
            return None
        return entry

    async def _cached(self, key: tuple[str, ...], produce: Callable[[], Any]) -> Any:
        """Return cached value for ``key`` or compute it once (single-flight).

        ``produce`` is an async callable. Its exception is cached for
        ``negative_ttl`` seconds and re-raised to every waiter.
        """
        entry = self._lookup(key)
        if entry is None:
            lock = self._locks.setdefault(key, asyncio.Lock())
            async with lock:
                entry = self._lookup(key)  # another waiter may have filled it
                if entry is None:
                    try:
                        value = await produce()
                    except Exception as exc:  # noqa: BLE001 - cached and re-raised
                        entry = _Entry(None, self._clock(), exc)
                    else:
                        entry = _Entry(value, self._clock())
                    self._cache[key] = entry
            if not lock.locked():
                self._locks.pop(key, None)
        if entry.error is not None:
            raise entry.error
        return entry.value

    async def _download(self, path: str) -> str:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._max_concurrent)
        async with self._semaphore:
            return await asyncio.to_thread(self._fetch_page, path)

    async def _page(self, path: str, day: date) -> str:
        url = f"{fetcher.BASE_URL}{path}"
        return await self._cached(("page", url, day.isoformat()), lambda: self._download(path))

    # -- public API -------------------------------------------------------

    async def get_forecast(self, slug: str, title: str, mode: str) -> str:
        """Formatted forecast text for ``slug`` titled ``title``."""
        if mode not in MODES:
            raise ValueError(f"Unknown mode: {mode!r}")
        today = self._today()
        today_path = f"/pohoda/{slug}"

        if mode == "today":
            url = f"{fetcher.BASE_URL}{today_path}"

            async def build_today() -> Any:
                html = await self._page(today_path, today)
                return parse_forecast_day(html, source_url=url, city_name=title, forecast_date=today)

            model = await self._cached(("model", url, today.isoformat(), "day"), build_today)
            return format_message(model, title=title)

        if mode == "tomorrow":
            tomorrow = today + timedelta(days=1)
            # Key on the today URL: the tomorrow URL is only known from its tab bar.
            key = ("model", f"{fetcher.BASE_URL}{today_path}", today.isoformat(), "tomorrow")

            async def build_tomorrow() -> Any:
                today_html = await self._page(today_path, today)
                day_url = fetcher.extract_day_url(today_html, slug, 1)
                day_path = urlsplit(day_url).path
                html = await self._page(day_path, today)
                return parse_forecast_day(
                    html, source_url=day_url, city_name=title, forecast_date=tomorrow
                )

            model = await self._cached(key, build_tomorrow)
            return format_message(model, title=title)

        path = today_path if mode == "week" else f"{today_path}/10-dniv"
        url = f"{fetcher.BASE_URL}{path}"

        async def build_days() -> Any:
            html = await self._page(path, today)
            return parse_day_tabs(html, today, min_days=_MIN_DAYS[mode])

        days = await self._cached(("model", url, today.isoformat(), mode), build_days)
        return format_multiday(title, days, mode, source_url=url)


_default_service: ForecastService | None = None


def default_service() -> ForecastService:
    global _default_service
    if _default_service is None:
        _default_service = ForecastService()
    return _default_service


async def get_forecast(slug: str, title: str, mode: str) -> str:
    """Module-level shortcut using the process-wide service."""
    return await default_service().get_forecast(slug, title, mode)
