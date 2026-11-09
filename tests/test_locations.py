"""Tests for weather_bot.locations (no network: MockTransport + fake fetch/parse)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from weather_bot import locations as L


@pytest.fixture(autouse=True)
def _clean_cache():
    L.clear_cache()
    yield
    L.clear_cache()


@pytest.mark.parametrize(
    "src,expected",
    [
        ("Дрогобич", "drohobych"), ("Стрий", "stryi"), ("Борислав", "boryslav"),
        ("Київ", "kyiv"), ("Львів", "lviv"), ("Іванівка", "ivanivka"),
        ("Трускавець", "truskavets"), ("Згурівка", "zghurivka"), ("Ясіня", "yasinia"),
        ("Знам'янка", "znamianka"), ("Пʼятихатки", "piatykhatky"),
        ("Їжакевичі", "yizhakevychi"),
        ("Кам'янець-Подільський", "kamianets-podilskyi"),
        ("Біла Церква", "bila-tserkva"),
    ],
)
def test_translit(src: str, expected: str) -> None:
    assert L.translit_uk(src) == expected


def test_haversine() -> None:
    assert L.haversine(50.0, 24.0, 50.0, 24.0) == 0
    # Lviv -> Kyiv ~ 468 km
    assert 460 < L.haversine(49.8397, 24.0297, 50.4501, 30.5234) < 475
    # 0.1 deg latitude ~ 11.1 km
    assert 11.0 < L.haversine(49.0, 23.0, 49.1, 23.0) < 11.3


# --- fakes -----------------------------------------------------------------------

PAGES: dict[str, dict[str, Any]] = {
    "stryi": {"id": "stryi", "title": "Стрий", "description": "Львівська область",
              "coordinates": {"lat": 49.2622, "lng": 23.8561}},
    "ivanivka": {"id": "ivanivka", "title": "Іванівка", "description": "Херсонська область",
                 "coordinates": {"lat": 46.97, "lng": 34.55}},
    "kyiv": {"id": "kyiv", "title": "Київ", "description": "", "coordinates": {"lat": 50.45, "lng": 30.52}},
}


class FakeSite:
    def __init__(self) -> None:
        self.fetched: list[str] = []

    def fetch(self, slug: str) -> str | None:
        self.fetched.append(slug)
        return slug if slug in PAGES else None  # None == 404/410

    @staticmethod
    def parse(html: str) -> dict[str, Any] | None:
        return PAGES.get(html)


def geo_transport(by_lang: dict[str, list[dict[str, Any]]], calls: list[str] | None = None):
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "geocoding-api.open-meteo.com"
        lang = req.url.params["language"]
        assert req.url.params["countryCode"] == "UA"
        if calls is not None:
            calls.append(lang)
        res = [h for h in by_lang.get(lang) or [] if req.url.params["name"] != "Ніщо"]
        return httpx.Response(200, json={"results": res} if res else {})
    return httpx.MockTransport(handler)


def resolve(q: str, transport: httpx.BaseTransport, site: FakeSite | None = None):
    site = site or FakeSite()
    return L.resolve_candidates(q, fetch_page=site.fetch, parse_state=site.parse, transport=transport)


def test_match() -> None:
    t = geo_transport({"uk": [{"name": "Стрий", "latitude": 49.26, "longitude": 23.85, "population": 59000}]})
    [c] = resolve("Стрий", t)
    assert (c.slug, c.title, c.population) == ("stryi", "Стрий", 59000)


def test_mismatch_over_15km() -> None:
    # Ternopil-oblast Іванівка, but slug "ivanivka" is the Kherson one
    t = geo_transport({"uk": [{"name": "Іванівка", "latitude": 49.5, "longitude": 25.5}]})
    assert resolve("Іванівка", t) == []


def test_410_page() -> None:
    site = FakeSite()
    t = geo_transport({"uk": [{"name": "Нікуди", "latitude": 49.0, "longitude": 24.0}]})
    assert resolve("Нікуди", t, site) == []
    assert site.fetched == ["nikudy"]


def test_ru_fallback() -> None:
    calls: list[str] = []
    t = geo_transport({"ru": [{"name": "Київ", "latitude": 50.45, "longitude": 30.52, "population": 2900000}]}, calls)
    [c] = resolve("Киев", t)
    assert c.slug == "kyiv" and calls == ["uk", "ru"]


def test_dedup_sort_and_limit() -> None:
    hits = [
        {"name": "Стрий", "latitude": 49.26, "longitude": 23.85, "population": 10},
        {"name": "Стрий", "latitude": 40.0, "longitude": 20.0, "population": 99},  # same slug, far
        {"name": "Київ", "latitude": 50.45, "longitude": 30.52, "population": 1000},
    ] + [{"name": f"Село{i}", "latitude": 1, "longitude": 1} for i in range(8)]
    site = FakeSite()
    res = resolve("Будь-що", geo_transport({"uk": hits}), site)
    assert [c.slug for c in res] == ["kyiv", "stryi"]
    assert res[1].population == 10  # population from the nearby hit only
    assert len(site.fetched) == 5  # at most 5 page fetches


def test_cache_positive_only() -> None:
    calls: list[str] = []
    t = geo_transport({"uk": [{"name": "Стрий", "latitude": 49.26, "longitude": 23.85}]}, calls)
    resolve("Стрий", t)
    resolve("стрий", t)
    assert calls == ["uk"]
    resolve("Ніщо", t)
    resolve("Ніщо", t)
    assert calls == ["uk", "uk", "ru", "uk", "ru"]


def test_cache_expires() -> None:
    clock = [0.0]
    calls: list[str] = []
    t = geo_transport({"uk": [{"name": "Стрий", "latitude": 49.26, "longitude": 23.85}]}, calls)
    site = FakeSite()
    for ts in (0.0, 100.0, L.CACHE_TTL_SEC + 1):
        clock[0] = ts
        L.resolve_candidates("Стрий", fetch_page=site.fetch, parse_state=site.parse,
                             transport=t, now=lambda: clock[0])
    assert calls == ["uk", "uk"]


def test_slug_escape_hatch_skips_geocoder() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise AssertionError("geocoder must not be called")
    [c] = resolve("stryi", httpx.MockTransport(boom))
    assert c.slug == "stryi"
    assert resolve("no-such-slug", httpx.MockTransport(boom)) == []


def test_geocoder_failure_raises() -> None:
    t = httpx.MockTransport(lambda req: httpx.Response(500))
    with pytest.raises(L.GeocoderError):
        resolve("Стрий", t)
    t2 = httpx.MockTransport(lambda req: (_ for _ in ()).throw(httpx.ConnectTimeout("t")))
    with pytest.raises(L.GeocoderError):
        resolve("Стрий", t2)


def test_parse_state_wrapper_accepted() -> None:
    site = FakeSite()
    t = geo_transport({"uk": [{"name": "Стрий", "latitude": 49.26, "longitude": 23.85}]})
    [c] = L.resolve_candidates("Стрий", fetch_page=site.fetch,
                               parse_state=lambda h: {"location": PAGES[h]}, transport=t)
    assert c.slug == "stryi"


@pytest.mark.parametrize("page_id", ["../api/x", "a/b", "x?y=1", "UPPER"])
def test_page_id_must_be_slug(page_id: str) -> None:
    PAGES["stryi-evil"] = dict(PAGES["stryi"], id=page_id)
    try:
        [c] = resolve("stryi-evil", geo_transport({}))
        assert c.slug == "stryi-evil"  # falls back to the requested slug
    finally:
        del PAGES["stryi-evil"]


def test_storage_rejects_bad_slug(tmp_path) -> None:
    from weather_bot.bot import storage
    db = tmp_path / "t.db"
    storage.migrate(db)
    with pytest.raises(ValueError):
        storage.add_location(db, 1, "X", "../api/x")


def test_integration_with_real_parse_location_state():
    """Regression: resolve_candidates must accept the LocationState that the
    real multiday.parse_location_state returns (was silently dropping all)."""
    from pathlib import Path

    import httpx

    from weather_bot.locations import clear_cache, resolve_candidates
    from weather_bot.multiday import parse_location_state

    html = (Path(__file__).parent / "fixtures" / "drohobych_week.html").read_text(encoding="utf-8")
    state = parse_location_state(html)
    geo = {"results": [{"name": "Дрогобич", "latitude": state.lat, "longitude": state.lng, "population": 73682}]}
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=geo))

    clear_cache()
    cands = resolve_candidates(
        "Дрогобич",
        fetch_page=lambda slug: html if slug == "drohobych" else None,
        parse_state=parse_location_state,
        transport=transport,
    )
    assert [c.slug for c in cands] == ["drohobych"]
