from datetime import date, time

import pytest

from weather_bot.formatter import _emoji_for, format_message, format_multiday, split_message
from weather_bot.models import DaySummary, ForecastDay, HourSlot

DAYS = [
    DaySummary(date(2026, 11, 9), "понеділок", -5, 6, "Ясно"),
    DaySummary(date(2026, 11, 10), "вівторок", -3, 9, "Суцільна хмарність,\nдощ"),
    DaySummary(date(2026, 11, 11), "середа", 0, 5, "Хмарно з проясненнями"),
]


def test_week_exact_output():
    text = format_multiday("Дрогобич", DAYS, "week", source_url="https://sinoptik.ua/pohoda/drohobych")
    assert text == (
        "📅 <b>Дрогобич — тиждень</b>\n"
        "<code>Пн 09.11  -5…+6 </code> ☀️ Ясно\n"
        "<code>Вт 10.11  -3…+9 </code> 🌧 Суцільна хмарність, дощ\n"
        "<code>Ср 11.11  0…+5  </code> ⛅ Хмарно з проясненнями\n"
        '<i>Джерело: <a href="https://sinoptik.ua/pohoda/drohobych">sinoptik.ua</a></i>'
    )


def test_10days_digest_exact_output():
    text = format_multiday(
        "Стрий", DAYS[:1], "10days", source_url="https://sinoptik.ua/pohoda/stryi/10-dniv", digest=True
    )
    assert text == (
        "🗞 <b>Тижневий огляд</b>\n"
        "📅 <b>Стрий — 10 днів</b>\n"
        "<code>Пн 09.11  -5…+6 </code> ☀️ Ясно\n"
        '<i>Джерело: <a href="https://sinoptik.ua/pohoda/stryi/10-dniv">sinoptik.ua</a></i>'
    )


def test_multiday_escapes_and_length():
    days = [DaySummary(date(2026, 11, 9), "понеділок", 1, 2, "<b>x</b> & y")] * 10
    text = format_multiday("A<B", days, "10days")
    assert "<b>x</b> &" not in text
    assert "&lt;b&gt;x&lt;/b&gt; &amp; y" in text
    assert "A&lt;B" in text
    assert len(text) < 4096


def test_unknown_kind_rejected():
    with pytest.raises(ValueError):
        format_multiday("X", DAYS, "month")


@pytest.mark.parametrize(
    ("condition", "emoji"),
    [
        ("Суцільна хмарність, дощ", "🌧"),
        ("Суцільна хмарність,\nмокрий сніг", "🌨"),
        ("Суцільна хмарність", "☁️"),
        ("Туман", "🌫"),
        ("Хмарно з проясненнями, невеликий дощ", "🌦"),
        ("Хмарно, гроза", "⛈"),
        ("Невелика хмарність", "🌤"),
        ("Ясно", "☀️"),
        (None, "🌡"),
        ("Щось нове", "🌡"),
    ],
)
def test_emoji_priority(condition, emoji):
    assert _emoji_for(condition) == emoji


def _forecast() -> ForecastDay:
    slot = HourSlot("12:00", "Ясно", 5, 3, 740, 60, 2.0, None, None)
    return ForecastDay("Дрогобич", date(2026, 11, 9), "https://sinoptik.ua/pohoda/stryi",
                       time(7, 20), time(16, 50), None, [slot])


def test_format_message_title():
    assert "Погода в Дрогобич" in format_message(_forecast())
    text = format_message(_forecast(), title="Стрий & Co")
    assert "Погода в Стрий &amp; Co" in text
    assert "Погода в Дрогобич" not in text


def test_split_message_on_line_boundaries():
    text = "\n".join(f"line {i:04d} " + "x" * 90 for i in range(100))
    parts = split_message(text, limit=1000)
    assert len(parts) > 1
    assert all(len(p) <= 1000 for p in parts)
    assert "\n".join(parts) == text
    assert split_message("short") == ["short"]
