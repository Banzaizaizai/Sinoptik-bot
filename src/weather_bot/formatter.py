from __future__ import annotations

from html import escape

from .models import ForecastDay

_CONDITION_EMOJI = {
    "ясно": "☀️",
    "мінлива хмарність": "⛅",
    "невелика хмарність": "🌤",
    "хмарно з проясненнями": "⛅",
    "хмарно": "☁️",
    "похмуро": "☁️",
    "дощ": "🌧",
    "невеликий дощ": "🌦",
    "дрібний дощ": "🌦",
    "гроза": "⛈",
    "сніг": "🌨",
}


def _emoji_for(condition: str | None) -> str:
    if not condition:
        return "🌡"
    key = condition.lower()
    for marker, emoji in _CONDITION_EMOJI.items():
        if marker in key:
            return emoji
    return "🌡"


def format_message(forecast: ForecastDay) -> str:
    lines: list[str] = []

    header_emoji = _emoji_for(forecast.slots[len(forecast.slots) // 2].condition if forecast.slots else None)
    date_str = forecast.forecast_date.strftime("%d.%m")
    lines.append(
        f"{header_emoji} <b>Погода в {escape(forecast.city_name)} — "
        f"{escape(forecast.weekday_label)}, {date_str}</b>"
    )

    if forecast.sunrise and forecast.sunset:
        lines.append(
            f"🌅 Схід: {forecast.sunrise.strftime('%H:%M')}   "
            f"🌇 Захід: {forecast.sunset.strftime('%H:%M')}"
        )
    lines.append(f"🌡 Мін: {forecast.min_temp_c:+d}°   Макс: {forecast.max_temp_c:+d}°")
    lines.append("")

    for slot in forecast.slots:
        precip = f"{slot.precip_probability_pct}%" if slot.precip_probability_pct is not None else "—"
        wind_dir = f" {slot.wind_dir}" if slot.wind_dir else ""
        lines.append(
            f"<code>{slot.time_label}</code>  {_emoji_for(slot.condition)} "
            f"{slot.temp_c:+d}° (відч. {slot.feels_like_c:+d}°)  "
            f"💧{slot.humidity_pct}%  🍃{slot.wind_ms:g} м/с{wind_dir}  ☔{precip}"
        )

    if forecast.summary_text:
        lines.append("")
        lines.append(f"📝 {escape(forecast.summary_text)}")

    lines.append("")
    lines.append(f'<i>Джерело: <a href="{escape(forecast.source_url)}">sinoptik.ua</a> '
                  f"(дані з кроком 3 години)</i>")

    return "\n".join(lines)
