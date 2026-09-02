from datetime import date, time

from weather_bot.formatter import format_message
from weather_bot.models import ForecastDay, HourSlot


def _sample_forecast() -> ForecastDay:
    slot = HourSlot(
        time_label="12:00",
        condition="Ясно",
        temp_c=23,
        feels_like_c=23,
        pressure_mmhg=739,
        humidity_pct=42,
        wind_ms=4.0,
        wind_dir="Південно-Східний",
        precip_probability_pct=None,
    )
    return ForecastDay(
        city_name="Дрогобич",
        forecast_date=date(2026, 8, 29),
        source_url="https://sinoptik.ua/pohoda/drohobych/subota",
        sunrise=time(6, 37),
        sunset=time(20, 15),
        summary_text="Хмарно, без опадів.",
        slots=[slot],
    )


def test_format_message_contains_key_facts():
    text = format_message(_sample_forecast())

    assert "Дрогобич" in text
    assert "субота" in text
    assert "29.08" in text
    assert "12:00" in text
    assert "+23°" in text
    assert "sinoptik.ua" in text
    assert len(text) <= 4096


def test_format_message_escapes_html():
    forecast = _sample_forecast()
    forecast = forecast.__class__(
        **{**forecast.__dict__, "summary_text": "<script>alert(1)</script>"}
    )
    text = format_message(forecast)
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
