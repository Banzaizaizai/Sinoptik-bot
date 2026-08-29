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


@dataclass(frozen=True)
class ForecastDay:
    city_name: str
    forecast_date: date
    weekday_label: str  # "субота", как на сайте
    source_url: str
    sunrise: time | None
    sunset: time | None
    summary_text: str | None
    slots: list[HourSlot]

    @property
    def min_temp_c(self) -> int:
        return min(s.temp_c for s in self.slots)

    @property
    def max_temp_c(self) -> int:
        return max(s.temp_c for s in self.slots)
