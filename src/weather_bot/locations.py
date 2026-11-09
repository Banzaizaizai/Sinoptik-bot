"""Location resolution: user text -> verified sinoptik.ua slugs.

Pipeline (design section 9):
  Open-Meteo geocoder (uk, then ru fallback) -> KMU-2010 transliteration -> slug ->
  GET /pohoda/<slug> -> embedded location JSON -> accept if within 15 km of the
  geocoder's coordinates.

Only ``/pohoda/...`` pages are fetched (via the injected ``fetch_page``); sinoptik's
``/api/`` paths are disallowed by robots.txt and are never used here.
"""

from __future__ import annotations

import math
import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

GEOCODER_URL = "https://geocoding-api.open-meteo.com/v1/search"
GEOCODER_TIMEOUT_SEC = 10.0
MAX_DISTANCE_KM = 15.0
MAX_CANDIDATES = 5
CACHE_TTL_SEC = 24 * 3600

SLUG_RE = re.compile(r"^[a-z0-9-]+$")

FetchPage = Callable[[str], "str | None"]
ParseState = Callable[[str], "dict[str, Any] | None"]


class GeocoderError(Exception):
    """The geocoder is unreachable or returned garbage."""


@dataclass(frozen=True)
class Candidate:
    slug: str
    title: str
    description: str
    lat: float
    lng: float
    population: int = 0


# --------------------------------------------------------------------------- translit

# Letters whose transliteration differs at the start of a word (KMU-2010).
_INITIAL = {"є": "ye", "ї": "yi", "й": "y", "ю": "yu", "я": "ya"}
_MAP = {
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e",
    "є": "ie", "ж": "zh", "з": "z", "и": "y", "і": "i", "ї": "i", "й": "i",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "shch", "ю": "iu", "я": "ia",
}
_DROP = {"ь", "'", "ʼ", "’", "`"}


def translit_uk(name: str) -> str:
    """Transliterate a Ukrainian place name into a sinoptik-style slug (KMU-2010)."""
    out: list[str] = []
    at_word_start = True
    prev = ""
    for ch in name.strip().lower():
        if ch in _DROP:
            continue  # apostrophes do not end a word: Знам'янка -> znamianka
        if ch in " -_":
            if out and out[-1] != "-":
                out.append("-")
            at_word_start = True
            prev = ""
            continue
        if at_word_start and ch in _INITIAL:
            out.append(_INITIAL[ch])
        elif ch == "г" and prev == "з":
            out.append("gh")  # зг -> zgh
        elif ch in _MAP:
            out.append(_MAP[ch])
        elif ch.isascii() and ch.isalnum():
            out.append(ch)
        # anything else (punctuation, Russian-only letters) is dropped
        at_word_start = False
        prev = ch
    return "".join(out).strip("-")


# --------------------------------------------------------------------------- geo

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def geocode(
    q: str,
    language: str = "uk",
    *,
    client: httpx.Client | None = None,
    transport: httpx.BaseTransport | None = None,
) -> list[dict[str, Any]]:
    """Query Open-Meteo geocoder for Ukrainian places. Raises GeocoderError on failure."""
    params = {"name": q, "count": 10, "language": language, "countryCode": "UA", "format": "json"}
    own = client is None
    c = client or httpx.Client(timeout=GEOCODER_TIMEOUT_SEC, transport=transport)
    try:
        r = c.get(GEOCODER_URL, params=params, timeout=GEOCODER_TIMEOUT_SEC)
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise GeocoderError(str(e)) from e
    finally:
        if own:
            c.close()
    if not isinstance(data, dict):
        raise GeocoderError("unexpected geocoder response")
    results = data.get("results") or []  # no "results" key == zero hits
    return [x for x in results if isinstance(x, dict) and x.get("name")]


# --------------------------------------------------------------------------- resolve

def _default_fetch(slug: str) -> str | None:
    from weather_bot.fetcher import fetch_location_page  # lazy: may not exist yet

    return fetch_location_page(slug)


def _default_parse(html: str) -> dict[str, Any] | None:
    from weather_bot.multiday import parse_location_state  # lazy: may not exist yet

    return parse_location_state(html)


def _location_from_state(state: dict[str, Any] | None) -> dict[str, Any] | None:
    """Accept either the location object itself or a wrapper with a "location" key."""
    if not isinstance(state, dict):
        return None
    loc = state.get("location") if isinstance(state.get("location"), dict) else state
    return loc


def _candidate(slug: str, html: str | None, parse_state: ParseState, population: int) -> Candidate | None:
    if not html:
        return None
    loc = _location_from_state(parse_state(html))
    if not loc:
        return None
    coords = loc.get("coordinates") or {}
    try:
        lat = float(coords.get("lat", loc.get("lat")))
        lng = float(coords.get("lng", loc.get("lng")))
    except (TypeError, ValueError):
        return None
    # The page-supplied id becomes a URL path segment forever; only trust a
    # plain slug, otherwise fall back to the slug we requested.
    page_id = str(loc.get("id") or "")
    if not SLUG_RE.match(page_id):
        page_id = slug
    if not SLUG_RE.match(page_id):
        return None
    return Candidate(
        slug=page_id,
        title=str(loc.get("title") or slug),
        description=str(loc.get("description") or ""),
        lat=lat,
        lng=lng,
        population=population,
    )


_cache: dict[str, tuple[float, list[Candidate]]] = {}


def clear_cache() -> None:
    _cache.clear()


def resolve_candidates(
    q: str,
    *,
    fetch_page: FetchPage | None = None,
    parse_state: ParseState | None = None,
    client: httpx.Client | None = None,
    transport: httpx.BaseTransport | None = None,
    now: Callable[[], float] = time.monotonic,
) -> list[Candidate]:
    """Resolve a user query into up to 5 verified sinoptik locations, by population desc.

    Input matching ``^[a-z0-9-]+$`` is treated as a raw slug (escape hatch): the page is
    fetched directly and no geocoder / distance check is involved.
    Raises GeocoderError if the geocoder is unavailable (caller shows a hint).
    """
    fetch = fetch_page or _default_fetch
    parse = parse_state or _default_parse
    key = q.strip().lower()
    hit = _cache.get(key)
    if hit and now() - hit[0] < CACHE_TTL_SEC:
        return list(hit[1])

    result: list[Candidate] = []
    if SLUG_RE.match(key):
        c = _candidate(key, fetch(key), parse, 0)
        result = [c] if c else []
    else:
        for lang in ("uk", "ru"):
            result = _resolve_via_geocoder(q.strip(), lang, fetch, parse, client, transport)
            if result:
                break

    if result:  # only positive results are cached
        _cache[key] = (now(), result)
    return list(result)


def _resolve_via_geocoder(
    q: str,
    lang: str,
    fetch: FetchPage,
    parse: ParseState,
    client: httpx.Client | None,
    transport: httpx.BaseTransport | None,
) -> list[Candidate]:
    hits = geocode(q, lang, client=client, transport=transport)
    # Build unique slugs, keeping every geocoder hit for the distance check (same-name
    # places share one slug; any of them within 15 km validates the page).
    by_slug: dict[str, list[dict[str, Any]]] = {}
    for h in hits:
        slug = translit_uk(str(h["name"]))
        if slug:
            by_slug.setdefault(slug, []).append(h)

    out: list[Candidate] = []
    for slug, group in list(by_slug.items())[:MAX_CANDIDATES]:  # sequential, max 5 fetches
        cand = _candidate(slug, fetch(slug), parse, 0)
        if cand is None:
            continue
        near = [
            h for h in group
            if haversine(cand.lat, cand.lng, float(h.get("latitude", 0)), float(h.get("longitude", 0)))
            <= MAX_DISTANCE_KM
        ]
        if not near:
            continue
        pop = max(int(h.get("population") or 0) for h in near)
        out.append(Candidate(cand.slug, cand.title, cand.description, cand.lat, cand.lng, pop))
    out.sort(key=lambda c: c.population, reverse=True)
    return out[:MAX_CANDIDATES]


if __name__ == "__main__":  # manual check: python -X utf8 -m weather_bot.locations Стрий
    query = " ".join(sys.argv[1:]) or "Стрий"
    print(f"translit: {translit_uk(query)}")
    try:
        for c in resolve_candidates(query):
            print(f"{c.slug:20} {c.title} | {c.description} | pop={c.population}")
    except GeocoderError as e:
        print(f"geocoder error: {e}")
