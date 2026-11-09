from datetime import date
from pathlib import Path

import pytest

from weather_bot.models import DaySummary
from weather_bot.multiday import parse_day_tabs, parse_location_state
from weather_bot.parser import ParseError

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 11, 9)


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _tab(weekday: str, day: str, month: str, t_min: str, t_max: str, cond: str, href: str) -> str:
    return (
        f'<a role="menuitem" href="{href}" '
        f'aria-label="Погода на {weekday} {day} {month}: від {t_min}° до {t_max}°">'
        f"<span>{weekday}</span><span>{day}</span><span>{month}</span>"
        f'<div aria-label="{cond}"></div></a>'
    )


def test_week_fixture_gives_seven_days():
    days = parse_day_tabs(_load("drohobych_week.html"), TODAY, min_days=6)
    assert len(days) == 7
    assert days[0] == DaySummary(date(2026, 11, 9), "понеділок", -5, 6, "Ясно")
    assert days[1] == DaySummary(date(2026, 11, 10), "вівторок", -3, 9, "Суцільна хмарність,\nдощ")
    assert days[4].weekday == "п'ятниця"  # U+02BC нормализован
    assert days[-1].date == date(2026, 11, 15)


def test_10days_fixture_gives_ten_days():
    days = parse_day_tabs(_load("drohobych_10days.html"), TODAY, min_days=9)
    assert len(days) == 10
    last = days[-1]
    assert (last.date, last.t_min, last.t_max) == (date(2026, 11, 18), 5, 10)
    assert last.condition == "Суцільна хмарність"


def test_min_days_validation():
    with pytest.raises(ParseError):
        parse_day_tabs(_load("drohobych_week.html"), TODAY, min_days=9)


def test_first_tab_must_be_today():
    with pytest.raises(ParseError):
        parse_day_tabs(_load("drohobych_week.html"), date(2026, 11, 8))


def test_tabs_removed_raises():
    with pytest.raises(ParseError):
        parse_day_tabs("<html><body><a role='menuitem'>Тиждень</a></body></html>", TODAY, min_days=6)


def test_year_rollover_and_unicode_minus():
    html = (
        _tab("четвер", "31", "грудня", "−3", "+1", "Сніг", "/pohoda/x")
        + _tab("пʼятниця", "01", "січня", "−7", "−2", "Ясно", "/pohoda/x/pyatnytsya")
    )
    days = parse_day_tabs(html, date(2026, 12, 31), min_days=2)
    assert [d.date for d in days] == [date(2026, 12, 31), date(2027, 1, 1)]
    assert (days[1].t_min, days[1].t_max) == (-7, -2)
    assert days[1].weekday == "п'ятниця"


def test_iso_href_takes_precedence():
    html = _tab("четвер", "31", "грудня", "1", "2", "Ясно", "/pohoda/x/10-dniv") + _tab(
        "пʼятниця", "01", "січня", "1", "2", "Ясно", "/pohoda/x/2027-01-01"
    )
    days = parse_day_tabs(html, date(2026, 12, 31))
    assert days[1].date == date(2027, 1, 1)


def test_non_consecutive_dates_raise():
    html = _tab("понеділок", "09", "листопада", "1", "2", "Ясно", "/pohoda/x") + _tab(
        "середа", "11", "листопада", "1", "2", "Ясно", "/pohoda/x/sereda"
    )
    with pytest.raises(ParseError):
        parse_day_tabs(html, TODAY)


@pytest.mark.parametrize("name", ["drohobych_week.html", "drohobych_10days.html", "drohobych_day4.html"])
def test_parse_location_state(name: str):
    loc = parse_location_state(_load(name))
    assert loc is not None
    assert loc.slug == "drohobych"
    assert loc.title == "Дрогобич"
    assert loc.description == "Львівська область, Дрогобицький район, Дрогобицька громада"
    assert loc.lat == pytest.approx(49.35, abs=0.01)
    assert loc.lng == pytest.approx(23.51, abs=0.01)


def test_parse_location_state_regex_fallback_and_missing():
    broken = '"location":{"id":"stryi","title":"Стрий","description":"Львівська область","coordinates":{"lat":49.26,"lng":23.85}'
    loc = parse_location_state(broken)  # JSON не закрыт — сработает regex
    assert loc is not None and loc.slug == "stryi" and loc.lng == pytest.approx(23.85)
    assert parse_location_state("<html></html>") is None
