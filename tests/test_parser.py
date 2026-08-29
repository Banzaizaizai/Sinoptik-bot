from datetime import date, time
from pathlib import Path

import pytest

from weather_bot.parser import ParseError, parse_tomorrow

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_today_page_smoke():
    html = _load("drohobych_today.html")
    forecast = parse_tomorrow(
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


def test_parse_tomorrow_page_weekday_label():
    html = _load("drohobych_tomorrow.html")
    forecast = parse_tomorrow(
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
        parse_tomorrow(
            "<html><body>нет данных</body></html>",
            source_url="https://sinoptik.ua/pohoda/drohobych",
            city_name="Дрогобич",
            forecast_date=date(2026, 8, 28),
        )
