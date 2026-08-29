from __future__ import annotations

import re
from datetime import date, time

from bs4 import BeautifulSoup
from bs4.element import Tag

from .models import ForecastDay, HourSlot

TIME_CELL_RE = re.compile(r"^\d{1,2}:00$")
DEGREE_RE = re.compile(r"[+\-−]?\d+")

EXPECTED_DATA_ROWS = 7  # иконка, темп., ощущается, давление, влажность, ветер, осадки


class ParseError(RuntimeError):
    """Вёрстка sinoptik.ua не соответствует ожидаемой — парсер требует ревизии."""


def parse_tomorrow(html: str, *, source_url: str, city_name: str, forecast_date: date) -> ForecastDay:
    soup = BeautifulSoup(html, "html.parser")

    table = _find_forecast_table(soup)
    time_labels = _extract_time_labels(table)
    rows = _extract_data_rows(table)
    icon_row, temp_row, feels_row, pressure_row, humidity_row, wind_row, precip_row = rows

    icon_cells = icon_row.find_all("td", recursive=False)
    temp_cells = temp_row.find_all("td", recursive=False)
    feels_cells = feels_row.find_all("td", recursive=False)
    pressure_cells = pressure_row.find_all("td", recursive=False)
    humidity_cells = humidity_row.find_all("td", recursive=False)
    wind_cells = wind_row.find_all("td", recursive=False)
    precip_cells = precip_row.find_all("td", recursive=False)

    slots: list[HourSlot] = []
    for i, time_label in enumerate(time_labels):
        slots.append(
            HourSlot(
                time_label=time_label,
                condition=_aria_label(icon_cells[i]),
                temp_c=_parse_degrees(temp_cells[i].get_text(strip=True)),
                feels_like_c=_parse_degrees(feels_cells[i].get_text(strip=True)),
                pressure_mmhg=_parse_int(pressure_cells[i].get_text(strip=True)),
                humidity_pct=_parse_int(humidity_cells[i].get_text(strip=True)),
                wind_ms=_parse_float(wind_cells[i].get_text(strip=True)),
                wind_dir=_aria_label(wind_cells[i]),
                precip_probability_pct=_parse_precip(precip_cells[i].get_text(strip=True)),
            )
        )

    sunrise, sunset = _find_sunrise_sunset(soup)
    weekday_label = _find_weekday_label(soup) or ""
    summary_text = _find_summary(table)

    return ForecastDay(
        city_name=city_name,
        forecast_date=forecast_date,
        weekday_label=weekday_label,
        source_url=source_url,
        sunrise=sunrise,
        sunset=sunset,
        summary_text=summary_text,
        slots=slots,
    )


def _find_forecast_table(soup: BeautifulSoup) -> Tag:
    for table in soup.find_all("table"):
        thead = table.find("thead")
        if not thead:
            continue
        head_rows = thead.find_all("tr")
        if len(head_rows) < 2:
            continue
        cells = head_rows[1].find_all("td")
        texts = [c.get_text(strip=True) for c in cells]
        if len(texts) == 8 and all(TIME_CELL_RE.match(t) for t in texts):
            return table
    raise ParseError(
        "Не найдена таблица почасового прогноза (ожидалась thead-строка из 8 ячеек "
        "вида '0:00'..'21:00'). Вероятно, изменилась вёрстка sinoptik.ua."
    )


def _extract_time_labels(table: Tag) -> list[str]:
    thead = table.find("thead")
    cells = thead.find_all("tr")[1].find_all("td")
    labels = []
    for c in cells:
        text = c.get_text(strip=True)  # "0:00"
        hour, _, minute = text.partition(":")
        labels.append(f"{int(hour):02d}:{minute}")
    return labels


def _extract_data_rows(table: Tag) -> list[Tag]:
    tbody = table.find("tbody")
    if tbody is None:
        raise ParseError("В таблице прогноза нет <tbody>.")
    rows = tbody.find_all("tr", recursive=False)
    if len(rows) != EXPECTED_DATA_ROWS:
        raise ParseError(
            f"Ожидалось {EXPECTED_DATA_ROWS} строк данных в таблице прогноза, "
            f"найдено {len(rows)}. Вероятно, изменилась вёрстка sinoptik.ua."
        )
    return rows


def _aria_label(cell: Tag) -> str | None:
    el = cell.find(attrs={"aria-label": True})
    if el is None:
        return None
    label = el["aria-label"]
    return label.replace("\n", " ").strip() or None


def _parse_degrees(text: str) -> int:
    match = DEGREE_RE.search(text)
    if not match:
        raise ParseError(f"Не удалось разобрать значение температуры: {text!r}")
    return int(match.group(0).replace("−", "-"))


def _parse_int(text: str) -> int:
    match = DEGREE_RE.search(text)
    if not match:
        raise ParseError(f"Не удалось разобрать числовое значение: {text!r}")
    return int(match.group(0).replace("−", "-"))


def _parse_float(text: str) -> float:
    cleaned = text.replace(",", ".").strip()
    try:
        return float(cleaned)
    except ValueError as exc:
        raise ParseError(f"Не удалось разобрать значение скорости ветра: {text!r}") from exc


def _parse_precip(text: str) -> int | None:
    text = text.strip()
    if text in ("-", "", "—"):
        return None
    return _parse_int(text)


def _find_sunrise_sunset(soup: BeautifulSoup) -> tuple[time | None, time | None]:
    for p in soup.find_all("p"):
        text = p.get_text(strip=True)
        if text.startswith("Схід"):
            spans = p.find_all("span")
            if len(spans) >= 2:
                return _parse_time(spans[0].get_text(strip=True)), _parse_time(spans[1].get_text(strip=True))
    return None, None


def _parse_time(text: str) -> time | None:
    match = re.match(r"^(\d{1,2}):(\d{2})$", text.strip())
    if not match:
        return None
    return time(int(match.group(1)), int(match.group(2)))


def _find_weekday_label(soup: BeautifulSoup) -> str | None:
    for p in soup.find_all("p"):
        if p.get_text(strip=True).startswith("Схід"):
            date_div = p.parent.find_previous_sibling("div") if p.parent else None
            if date_div:
                ps = date_div.find_all("p")
                if ps:
                    return ps[0].get_text(strip=True)
    return None


def _find_summary(table: Tag) -> str | None:
    container = table.find_parent()
    if container is None:
        return None
    p = container.find_next_sibling("p")
    if p is None:
        return None
    text = p.get_text(strip=True)
    if not text or text.startswith("Народний прогноз"):
        return None
    return text
