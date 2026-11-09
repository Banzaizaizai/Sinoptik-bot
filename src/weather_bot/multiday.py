"""Разбор многодневных данных sinoptik.ua: таб-бар дней и встроенная локация.

Таб-бар дней есть на любой странице /pohoda/<slug>[...]: на "сегодняшней"
странице — 7 вкладок, на /10-dniv — 10. Каждая вкладка — <a role="menuitem">
с aria-label вида "Погода у Дрогобичі на понеділок 09 листопада: від -5° до +6°".
Классы CSS на сайте хешированные, поэтому опираемся только на role/aria-label
и структуру.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta

from bs4 import BeautifulSoup
from bs4.element import Tag

from .models import DaySummary, LocationState
from .parser import DAY_TAB_RANGE_RE, ParseError

_ISO_DATE_HREF_RE = re.compile(r"/(\d{4}-\d{2}-\d{2})/?$")
_LOCATION_KEY = '"location":{'
# Запасной вариант, если raw_decode не справился (например, обрезанный JSON).
_LOCATION_FALLBACK_RE = re.compile(
    r'"location":\{"id":"([^"]+)".*?"title":"([^"]+)".*?"description":"([^"]*)"'
    r'.*?"lat":(-?[\d.]+),"lng":(-?[\d.]+)',
    re.S,
)
_MONTHS_GENITIVE = {
    m: i + 1
    for i, m in enumerate(
        "січня лютого березня квітня травня червня липня серпня вересня жовтня листопада грудня".split()
    )
}


def _to_int(text: str) -> int:
    return int(text.replace("−", "-").replace("+", ""))


def _normalize_weekday(text: str) -> str:
    # В span'е апостроф U+02BC ("пʼятниця"), в aria-label — ASCII.
    return text.replace("ʼ", "'").replace("’", "'").strip()


def _day_tabs(soup: BeautifulSoup) -> list[tuple[Tag, re.Match[str]]]:
    tabs: list[tuple[Tag, re.Match[str]]] = []
    for a in soup.find_all("a", attrs={"role": "menuitem", "aria-label": True}):
        m = DAY_TAB_RANGE_RE.search(a["aria-label"])
        if m:
            tabs.append((a, m))
    return tabs


def _tab_date(a: Tag, spans: list[str], today: date) -> date:
    """Дата вкладки: из ISO-href, иначе из "день + месяц" с учётом перехода года."""
    href = a.get("href") or ""
    iso = _ISO_DATE_HREF_RE.search(str(href))
    if iso:
        return date.fromisoformat(iso.group(1))
    if len(spans) < 3:
        raise ParseError(f"Во вкладке дня нет дня/месяца: {spans!r}")
    try:
        day_num = int(spans[1])
        month = _MONTHS_GENITIVE[spans[2].lower()]
    except (ValueError, KeyError) as exc:
        raise ParseError(f"Не удалось разобрать дату вкладки: {spans!r}") from exc
    # Вкладки идут вперёд от сегодня, поэтому месяц "меньше текущего" — это уже следующий год.
    year = today.year + (1 if month < today.month else 0)
    try:
        return date(year, month, day_num)
    except ValueError as exc:
        raise ParseError(f"Некорректная дата вкладки: {spans!r}") from exc


def parse_day_tabs(html: str, today: date, *, min_days: int = 1) -> list[DaySummary]:
    """Возвращает сводки по дням из таб-бара.

    Строгая валидация (чтобы не отправить пользователю мусор при смене вёрстки):
    дней не меньше ``min_days`` (неделя — 6, 10 днів — 9), даты идут подряд,
    первая — ``today`` (дата по Киеву). Иначе — ``ParseError``.
    """
    soup = BeautifulSoup(html, "html.parser")
    days: list[DaySummary] = []
    for a, rng in _day_tabs(soup):
        spans = [s.get_text(strip=True) for s in a.find_all("span", recursive=False)]
        if not spans:
            raise ParseError("Во вкладке дня нет названия дня недели.")
        cond_el = a.find(lambda t: t.name == "div" and t.has_attr("aria-label"))
        days.append(
            DaySummary(
                date=_tab_date(a, spans, today),
                weekday=_normalize_weekday(spans[0]),
                t_min=_to_int(rng.group(1)),
                t_max=_to_int(rng.group(2)),
                condition=str(cond_el["aria-label"]).strip() if cond_el else "",
            )
        )

    if len(days) < min_days:
        raise ParseError(
            f"Найдено {len(days)} вкладок дней, ожидалось не меньше {min_days}. "
            "Вероятно, изменилась вёрстка sinoptik.ua."
        )
    if days and days[0].date != today:
        raise ParseError(f"Первая вкладка — {days[0].date}, ожидалась сегодняшняя дата {today}.")
    for prev, cur in zip(days, days[1:]):
        if cur.date != prev.date + timedelta(days=1):
            raise ParseError(f"Даты вкладок идут не подряд: {prev.date} → {cur.date}.")
    return days


def parse_location_state(html: str) -> LocationState | None:
    """Достаёт объект "location" из встроенного JSON-состояния страницы."""
    idx = html.find(_LOCATION_KEY)
    if idx < 0:
        return None
    start = idx + len(_LOCATION_KEY) - 1  # позиция "{"
    try:
        obj, _ = json.JSONDecoder().raw_decode(html, start)
    except ValueError:
        obj = None
    if isinstance(obj, dict) and obj.get("id") and obj.get("title"):
        coords = obj.get("coordinates") or {}
        lat, lng = coords.get("lat"), coords.get("lng")
        return LocationState(
            slug=str(obj["id"]),
            title=str(obj["title"]),
            description=obj.get("description") or None,
            lat=float(lat) if lat is not None else None,
            lng=float(lng) if lng is not None else None,
        )
    m = _LOCATION_FALLBACK_RE.search(html, idx)
    if not m:
        return None
    return LocationState(
        slug=m.group(1),
        title=m.group(2),
        description=m.group(3) or None,
        lat=float(m.group(4)),
        lng=float(m.group(5)),
    )
