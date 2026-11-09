from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time


@dataclass(frozen=True)
class HourSlot:
    """Один срез прогноза (шаг 3 часа — реальное разрешение данных на sinoptik.ua)."""

    time_label: str  # "00:00".."21:00"
    condition: str | None  # "Ясно", "Хмарно з проясненнями", ...
    temp_c: int
    feels_like_c: int
    pressure_mmhg: int
    humidity_pct: int
    wind_ms: float
    wind_dir: str | None
    precip_probability_pct: int | None  # None, если сайт не даёт значение ("-")


_UA_WEEKDAYS = ["понеділок", "вівторок", "середа", "четвер", "п'ятниця", "субота", "неділя"]


@dataclass(frozen=True)
class ForecastDay:
    city_name: str
    forecast_date: date
    source_url: str
    sunrise: time | None
    sunset: time | None
    summary_text: str | None
    slots: list[HourSlot]

    @property
    def weekday_label(self) -> str:
        # Считаем из forecast_date, а не парсим со страницы: на "сегодняшней"
        # странице sinoptik.ua вместо блока с днём недели показан виджет
        # текущей погоды — рядом с восходом/закатом там нет надёжного
        # текстового маркера дня недели.
        return _UA_WEEKDAYS[self.forecast_date.weekday()]

    @property
    def min_temp_c(self) -> int:
        return min(s.temp_c for s in self.slots)

    @property
    def max_temp_c(self) -> int:
        return max(s.temp_c for s in self.slots)


@dataclass(frozen=True)
class DaySummary:
    """Краткая сводка по дню из таб-бара дней (страницы "тиждень"/"10 днів")."""

    date: date
    weekday: str  # как на сайте, апостроф нормализован к ASCII: "п'ятниця"
    t_min: int
    t_max: int
    condition: str  # может содержать "\n" ("Суцільна хмарність,\nдощ"); "" если нет


@dataclass(frozen=True)
class LocationState:
    """Локация, встроенная в JSON-состояние страницы /pohoda/<slug>."""

    slug: str
    title: str
    description: str | None
    lat: float | None
    lng: float | None
