from __future__ import annotations

import logging
import re

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

BASE_URL = "https://sinoptik.ua"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 WeatherBotDrohobych/1.0"
)

# Вкладки дней на sinoptik.ua помечены role="menuitem" и содержат в aria-label
# диапазон "від Xдо Y" — по этому признаку отличаем их от других menuitem
# на странице (переключатели "Тиждень"/"10 днів" тоже role="menuitem", но
# без диапазона температур в aria-label).
DAY_TAB_RANGE_RE = re.compile(r":\s*від\s*[+\-−]?\d+°\s*до\s*[+\-−]?\d+°")


class FetchError(RuntimeError):
    pass


def _client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "uk-UA,uk;q=0.9"},
        timeout=15.0,
        follow_redirects=True,
    )


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
)
def _get(client: httpx.Client, url: str) -> str:
    logger.info("GET %s", url)
    resp = client.get(url)
    resp.raise_for_status()
    return resp.text


def fetch_tomorrow_html(city_slug: str) -> tuple[str, str]:
    """Возвращает (html, url) страницы прогноза на завтра.

    Завтрашний день определяется не хардкодом slug'а дня недели, а по
    положению в таб-баре дней на сегодняшней странице: первая вкладка —
    сегодня, вторая — завтра. Это устойчиво к смене недели/локализации.
    """
    today_url = f"{BASE_URL}/pohoda/{city_slug}"

    with _client() as client:
        today_html = _get(client, today_url)
        tomorrow_url = _extract_tomorrow_url(today_html, city_slug)
        tomorrow_html = _get(client, tomorrow_url)

    return tomorrow_html, tomorrow_url


def _extract_tomorrow_url(today_html: str, city_slug: str) -> str:
    soup = BeautifulSoup(today_html, "html.parser")

    day_tabs = [
        a
        for a in soup.find_all("a", attrs={"role": "menuitem", "aria-label": True})
        if DAY_TAB_RANGE_RE.search(a["aria-label"])
    ]

    if len(day_tabs) < 2:
        raise FetchError(
            f"Не удалось найти вкладки дней на странице {BASE_URL}/pohoda/{city_slug} "
            f"(найдено {len(day_tabs)}). Вероятно, изменилась вёрстка сайта."
        )

    tomorrow_href = day_tabs[1].get("href")
    if not tomorrow_href:
        raise FetchError("У вкладки 'завтра' нет href.")

    if tomorrow_href.startswith("http"):
        return tomorrow_href
    return f"{BASE_URL}{tomorrow_href}"
