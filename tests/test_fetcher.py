from pathlib import Path

import httpx
import pytest

from weather_bot import fetcher
from weather_bot.fetcher import (
    FetchError,
    extract_day_url,
    fetch_10days_html,
    fetch_html,
    fetch_location_page,
    fetch_today_html,
    fetch_tomorrow_html,
    fetch_week_html,
)

FIXTURES = Path(__file__).parent / "fixtures"
WEEK_HTML = (FIXTURES / "drohobych_week.html").read_text(encoding="utf-8")


@pytest.fixture
def routes(monkeypatch):
    """Подменяет HTTP-клиент на MockTransport. routes[path] = (status, body | redirect-path)."""
    table: dict[str, tuple[int, str]] = {}
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append(path)
        assert "/api/" not in path
        status, payload = table.get(path, (404, "not found"))
        if status in (301, 302):
            return httpx.Response(status, headers={"Location": payload})
        return httpx.Response(status, text=payload)

    def client() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)

    monkeypatch.setattr(fetcher, "_client", client)
    table_obj = type("Routes", (), {"table": table, "seen": seen})
    return table_obj


def test_fetch_html_ok(routes):
    routes.table["/pohoda/stryi"] = (200, "ok")
    assert fetch_html("/pohoda/stryi") == "ok"


def test_redirect_out_of_window_raises(routes):
    routes.table["/pohoda/drohobych/2026-11-20"] = (302, "/pohoda/drohobych")
    routes.table["/pohoda/drohobych"] = (200, "today")
    with pytest.raises(FetchError, match="redirected"):
        fetch_html("/pohoda/drohobych/2026-11-20")


@pytest.mark.parametrize("target", ["/pohoda/stryi/", "/ru/pohoda/stryi"])
def test_redirect_normalization_accepted(routes, target):
    routes.table["/pohoda/stryi"] = (302, target)
    routes.table[target] = (200, "ok")
    assert fetch_html("/pohoda/stryi") == "ok"


def test_only_pohoda_paths_allowed(routes):
    with pytest.raises(ValueError):
        fetch_html("/api/search/suggest")
    assert routes.seen == []


@pytest.mark.parametrize("status", [404, 410])
def test_location_page_missing(routes, status):
    routes.table["/pohoda/nowhere"] = (status, "")
    assert fetch_location_page("nowhere") is None


def test_location_page_redirected_to_home(routes):
    routes.table["/pohoda/nowhere"] = (301, "/")
    routes.table["/"] = (200, "home")
    assert fetch_location_page("nowhere") is None


def test_location_page_ok(routes):
    routes.table["/pohoda/boryslav"] = (200, "page")
    assert fetch_location_page("boryslav") == "page"


def test_week_and_10days_paths(routes):
    routes.table["/pohoda/stryi"] = (200, "week")
    routes.table["/pohoda/stryi/10-dniv"] = (200, "ten")
    assert fetch_week_html("stryi") == ("week", "https://sinoptik.ua/pohoda/stryi")
    assert fetch_10days_html("stryi") == ("ten", "https://sinoptik.ua/pohoda/stryi/10-dniv")


def test_default_slug_is_drohobych(routes):
    routes.table["/pohoda/drohobych"] = (200, WEEK_HTML)
    routes.table["/pohoda/drohobych/vivtorok"] = (200, "tomorrow")
    assert fetch_today_html() == (WEEK_HTML, "https://sinoptik.ua/pohoda/drohobych")
    assert fetch_tomorrow_html() == ("tomorrow", "https://sinoptik.ua/pohoda/drohobych/vivtorok")
    assert fetch_week_html()[1] == "https://sinoptik.ua/pohoda/drohobych"


def test_tomorrow_reuses_today_html(routes):
    routes.table["/pohoda/drohobych/vivtorok"] = (200, "tomorrow")
    html, url = fetch_tomorrow_html("drohobych", today_html=WEEK_HTML)
    assert html == "tomorrow"
    assert routes.seen == ["/pohoda/drohobych/vivtorok"]


def test_extract_day_url_by_index():
    assert extract_day_url(WEEK_HTML, "drohobych", 3) == "https://sinoptik.ua/pohoda/drohobych/chetver"
    with pytest.raises(FetchError):
        extract_day_url(WEEK_HTML, "drohobych", 7)
