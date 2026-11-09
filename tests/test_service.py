"""Tests for weather_bot.service (no network: fetch_page is a fake)."""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import date
from pathlib import Path

import pytest

from weather_bot.fetcher import FetchError
from weather_bot.service import ForecastService

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 11, 9)  # date of the week / 10-day fixtures


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeFetcher:
    """Serves fixtures by path and records calls."""

    def __init__(self, delay: float = 0.0) -> None:
        self.calls: list[str] = []
        self.delay = delay
        self.fail: Exception | None = None
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def __call__(self, path: str) -> str:
        with self._lock:
            self.calls.append(path)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
            if self.fail is not None:
                raise self.fail
            if path == "/pohoda/drohobych":
                return _load("drohobych_week.html")
            if path == "/pohoda/drohobych/10-dniv":
                return _load("drohobych_10days.html")
            if path.startswith("/pohoda/"):
                return _load("drohobych_day4.html")
            raise AssertionError(path)
        finally:
            with self._lock:
                self.active -= 1


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _service(fake: FakeFetcher, clock: Clock | None = None, **kw) -> ForecastService:
    return ForecastService(fetch_page=fake, clock=clock or Clock(), today=lambda: TODAY, ttl=600, **kw)


def test_week_and_today_share_one_fetch() -> None:
    fake = FakeFetcher()
    svc = _service(fake)

    async def go() -> tuple[str, str]:
        return (
            await svc.get_forecast("drohobych", "Дім", "week"),
            await svc.get_forecast("drohobych", "Дім", "today"),
        )

    week, today = asyncio.run(go())
    assert fake.calls == ["/pohoda/drohobych"]
    assert "Дім" in week and "09.11" in week
    assert "Дім" in today


def test_10days() -> None:
    fake = FakeFetcher()
    text = asyncio.run(_service(fake).get_forecast("drohobych", "X", "10days"))
    assert fake.calls == ["/pohoda/drohobych/10-dniv"]
    assert "18.11" in text


def test_tomorrow_reuses_cached_today_page() -> None:
    fake = FakeFetcher()
    svc = _service(fake)

    async def go() -> str:
        await svc.get_forecast("drohobych", "X", "today")
        return await svc.get_forecast("drohobych", "X", "tomorrow")

    asyncio.run(go())
    assert fake.calls[0] == "/pohoda/drohobych"
    assert len(fake.calls) == 2
    assert fake.calls[1].startswith("/pohoda/drohobych/")


def test_title_is_per_request_not_cached() -> None:
    fake = FakeFetcher()
    svc = _service(fake)

    async def go() -> tuple[str, str]:
        return (
            await svc.get_forecast("drohobych", "Перший", "week"),
            await svc.get_forecast("drohobych", "Другий", "week"),
        )

    a, b = asyncio.run(go())
    assert "Перший" in a and "Другий" in b
    assert len(fake.calls) == 1


def test_ttl_expiry_refetches() -> None:
    fake = FakeFetcher()
    clock = Clock()
    svc = _service(fake, clock)

    async def go() -> None:
        await svc.get_forecast("drohobych", "X", "week")
        clock.now += 599
        await svc.get_forecast("drohobych", "X", "week")
        clock.now += 2
        await svc.get_forecast("drohobych", "X", "week")

    asyncio.run(go())
    assert len(fake.calls) == 2


def test_new_kyiv_date_is_a_new_key() -> None:
    fake = FakeFetcher()
    days = iter([TODAY, TODAY, date(2026, 11, 10)])
    svc = ForecastService(fetch_page=fake, clock=Clock(), today=lambda: next(days), ttl=600)

    async def go() -> None:
        await svc.get_forecast("drohobych", "X", "today")
        await svc.get_forecast("drohobych", "X", "today")
        await svc.get_forecast("drohobych", "X", "today")

    asyncio.run(go())
    assert len(fake.calls) == 2


def test_single_flight() -> None:
    fake = FakeFetcher(delay=0.05)
    svc = _service(fake)

    async def go() -> list[str]:
        return await asyncio.gather(*(svc.get_forecast("drohobych", "X", "week") for _ in range(10)))

    results = asyncio.run(go())
    assert len(set(results)) == 1
    assert fake.calls == ["/pohoda/drohobych"]


def test_semaphore_limits_concurrency() -> None:
    fake = FakeFetcher(delay=0.05)
    svc = _service(fake)
    slugs = ["drohobych"] + [f"town{i}" for i in range(5)]

    async def go() -> None:
        # Other slugs serve day4 HTML: parse_day_tabs may reject it, which is fine here.
        await asyncio.gather(
            *(svc.get_forecast(s, "X", "10days") for s in slugs), return_exceptions=True
        )

    asyncio.run(go())
    assert len(fake.calls) == 6
    assert fake.max_active <= 2


def test_negative_cache() -> None:
    fake = FakeFetcher()
    fake.fail = FetchError("boom")
    clock = Clock()
    svc = _service(fake, clock)

    async def call() -> str:
        return await svc.get_forecast("drohobych", "X", "week")

    with pytest.raises(FetchError):
        asyncio.run(call())
    fake.fail = None
    clock.now += 30
    with pytest.raises(FetchError):  # still remembered
        asyncio.run(call())
    assert len(fake.calls) == 1
    clock.now += 31
    assert "X" in asyncio.run(call())
    assert len(fake.calls) == 2


def test_unknown_mode() -> None:
    with pytest.raises(ValueError):
        asyncio.run(_service(FakeFetcher()).get_forecast("drohobych", "X", "month"))
