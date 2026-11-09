from __future__ import annotations

from html import escape

from .models import DaySummary, ForecastDay

# Порядок важен: проверяем от более специфичного к общему и осадки раньше
# облачности, иначе "Суцільна хмарність, дощ" получит ☁️ вместо 🌧, а
# "мокрий сніг" — просто 🌨 через "сніг" (здесь это одно и то же, но маркер
# оставлен явно), "невеликий дощ" — 🌧 через "дощ".
_CONDITION_EMOJI: tuple[tuple[str, str], ...] = (
    ("гроза", "⛈"),
    ("мокрий сніг", "🌨"),
    ("сніг", "🌨"),
    ("невеликий дощ", "🌦"),
    ("дрібний дощ", "🌦"),
    ("дощ", "🌧"),
    ("туман", "🌫"),
    ("суцільна хмарність", "☁️"),
    ("мінлива хмарність", "⛅"),
    ("невелика хмарність", "🌤"),
    ("хмарно з проясненнями", "⛅"),
    ("хмарно", "☁️"),
    ("похмуро", "☁️"),
    ("ясно", "☀️"),
)

_WEEKDAY_ABBR = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд")

MULTIDAY_KINDS = {"week": "тиждень", "10days": "10 днів"}

# Лимит Telegram — 4096 символов; оставляем запас под разметку.
MESSAGE_SPLIT_LIMIT = 4000


def _emoji_for(condition: str | None) -> str:
    if not condition:
        return "🌡"
    key = " ".join(condition.lower().split())
    for marker, emoji in _CONDITION_EMOJI:
        if marker in key:
            return emoji
    return "🌡"


def format_message(forecast: ForecastDay, title: str | None = None) -> str:
    """Подробный прогноз на день. ``title`` — название локации для заголовка
    (по умолчанию ``forecast.city_name``, для канала это "Дрогобич")."""
    city = title if title is not None else forecast.city_name
    lines: list[str] = []

    header_emoji = _emoji_for(forecast.slots[len(forecast.slots) // 2].condition if forecast.slots else None)
    date_str = forecast.forecast_date.strftime("%d.%m")
    lines.append(
        f"{header_emoji} <b>Погода в {escape(city)} — "
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


def _fmt_temp(value: int) -> str:
    return "0" if value == 0 else f"{value:+d}"


def format_multiday(
    title: str,
    days: list[DaySummary],
    kind: str,
    *,
    source_url: str | None = None,
    digest: bool = False,
) -> str:
    """Многодневная сводка: по строке на день.

    ``kind`` — "week" или "10days". ``digest=True`` добавляет шапку
    "🗞 Тижневий огляд" (для еженедельной рассылки).
    """
    if kind not in MULTIDAY_KINDS:
        raise ValueError(f"Неизвестный вид сводки: {kind!r}")
    lines: list[str] = []
    if digest:
        lines.append("🗞 <b>Тижневий огляд</b>")
    lines.append(f"📅 <b>{escape(title)} — {MULTIDAY_KINDS[kind]}</b>")
    for day in days:
        rng = f"{_fmt_temp(day.t_min)}…{_fmt_temp(day.t_max)}"
        condition = " ".join(day.condition.replace("\n", " ").split())
        label = f"{_WEEKDAY_ABBR[day.date.weekday()]} {day.date.strftime('%d.%m')}"
        lines.append(f"<code>{label}  {rng:<6}</code> {_emoji_for(condition)} {escape(condition)}".rstrip())
    if source_url:
        lines.append(f'<i>Джерело: <a href="{escape(source_url)}">sinoptik.ua</a></i>')
    return "\n".join(lines)


def split_message(text: str, limit: int = MESSAGE_SPLIT_LIMIT) -> list[str]:
    """Режет текст на части не длиннее ``limit`` по границам строк."""
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
        # Одна строка длиннее лимита — режем жёстко (на практике не бывает).
        while len(line) > limit:
            parts.append(line[:limit])
            line = line[limit:]
        current = line
    if current or not parts:
        parts.append(current)
    return parts
