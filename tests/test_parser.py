from datetime import date, time
from pathlib import Path

import pytest

from weather_bot.parser import ParseError, parse_forecast_day

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_today_page_smoke():
    html = _load("drohobych_today.html")
    forecast = parse_forecast_day(
        html,
        source_url="https://sinoptik.ua/pohoda/drohobych",
        city_name="Дрогобич",
        forecast_date=date(2026, 8, 28),
    )

    assert forecast.city_name == "Дрогобич"
    assert len(forecast.slots) == 8
    assert [s.time_label for s in forecast.slots] == [
        "00:00", "03:00", "06:00", "09:00", "12:00", "15:00", "18:00", "21:00",
    ]

    first = forecast.slots[0]
    assert first.temp_c == 16
    assert first.feels_like_c == 16
    assert first.pressure_mmhg == 740
    assert first.humidity_pct == 58
    assert first.wind_ms == 2
    assert first.wind_dir == "Східний"
    assert first.condition == "Ясно"
    assert first.precip_probability_pct is None

    evening = forecast.slots[-1]
    assert evening.precip_probability_pct == 2

    assert forecast.min_temp_c == 12
    assert forecast.max_temp_c == 25
    assert forecast.sunrise == time(6, 36)
    assert forecast.sunset == time(20, 17)
    assert forecast.summary_text and "хмари" in forecast.summary_text
    assert "Народний прогноз" not in (forecast.summary_text or "")


def test_parse_forecast_day_page_weekday_label():
    html = _load("drohobych_tomorrow.html")
    forecast = parse_forecast_day(
        html,
        source_url="https://sinoptik.ua/pohoda/drohobych/subota",
        city_name="Дрогобич",
        forecast_date=date(2026, 8, 29),
    )

    assert forecast.weekday_label == "субота"
    assert len(forecast.slots) == 8
    assert forecast.slots[0].temp_c == 14


def test_parse_error_on_garbage_html():
    with pytest.raises(ParseError):
        parse_forecast_day(
            "<html><body>нет данных</body></html>",
            source_url="https://sinoptik.ua/pohoda/drohobych",
            city_name="Дрогобич",
            forecast_date=date(2026, 8, 28),
        )


def _synthetic_table(times: list[str]) -> str:
    head = "".join(f"<td>{t}</td>" for t in times)
    def row(val: str) -> str:
        return "<tr>" + "".join(f"<td>{val}</td>" for _ in times) + "</tr>"
    icon_row = "<tr>" + "".join('<td><div aria-label="Ясно"></div></td>' for _ in times) + "</tr>"
    wind_row = "<tr>" + "".join('<td><div aria-label="Західний">3,5</div></td>' for _ in times) + "</tr>"
    return (
        "<div><table><thead><tr><td>ніч</td><td>ранок</td><td>день</td><td>вечір</td></tr>"
        f"<tr>{head}</tr></thead><tbody>"
        + icon_row + row("+5°") + row("+3°") + row("740") + row("80") + wind_row + row("-")
        + "</tbody></table></div>"
    )


def test_parse_synthetic_four_slot_table():
    forecast = parse_forecast_day(
        _synthetic_table(["2:00", "8:00", "14:00", "20:00"]),
        source_url="u", city_name="Дрогобич", forecast_date=date(2026, 11, 12),
    )
    assert [s.time_label for s in forecast.slots] == ["02:00", "08:00", "14:00", "20:00"]
    assert forecast.slots[0].wind_ms == 3.5
    assert forecast.slots[0].precip_probability_pct is None


def test_unexpected_slot_count_rejected():
    with pytest.raises(ParseError):
        parse_forecast_day(
            _synthetic_table(["2:00", "8:00", "14:00"]),
            source_url="u", city_name="Дрогобич", forecast_date=date(2026, 11, 12),
        )


def test_parse_real_four_slot_day_page():
    forecast = parse_forecast_day(
        _load("drohobych_day4.html"),
        source_url="https://sinoptik.ua/pohoda/drohobych/chetver",
        city_name="Дрогобич",
        forecast_date=date(2026, 11, 12),
    )
    assert [s.time_label for s in forecast.slots] == ["02:00", "08:00", "14:00", "20:00"]
    first = forecast.slots[0]
    assert first.condition == "Суцільна хмарність, дощ"
    assert (first.temp_c, first.feels_like_c, first.pressure_mmhg, first.humidity_pct) == (3, 1, 727, 95)
    assert first.precip_probability_pct == 59
    assert (forecast.min_temp_c, forecast.max_temp_c) == (3, 9)
    assert forecast.sunrise == time(7, 31)


def test_day_tab_range_re_exported():
    from weather_bot import fetcher, parser

    assert fetcher.DAY_TAB_RANGE_RE is parser.DAY_TAB_RANGE_RE
    m = parser.DAY_TAB_RANGE_RE.search("на понеділок 09 листопада: від −5° до +6°")
    assert m and m.groups() == ("−5", "+6")
