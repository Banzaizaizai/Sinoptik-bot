from __future__ import annotations

import logging
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

# DAY_TAB_RANGE_RE живёт в parser.py (нужен и multiday.py); реэкспорт для совместимости.
from .parser import DAY_TAB_RANGE_RE

logger = logging.getLogger(__name__)

BASE_URL = "https://sinoptik.ua"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 WeatherBotDrohobych/1.0"
)

DEFAULT_CITY_SLUG = "drohobych"

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


def _normalize_path(path: str) -> str:
    """Путь для сравнения: без завершающего "/" и без языкового префикса "/ru"."""
    path = path.rstrip("/") or "/"
    if path == "/ru" or path.startswith("/ru/"):
        path = path[3:] or "/"
    return path


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
)
def _get_checked(client: httpx.Client, path: str, *, allow_missing: bool = False) -> str | None:
    """GET {BASE_URL}{path} с проверкой итогового URL после редиректов.

    sinoptik.ua отвечает на даты вне окна прогноза редиректом на сегодняшнюю
    страницу с HTTP 200, поэтому одного статуса мало: если итоговый путь
    (после нормализации "/" и "/ru/") отличается от запрошенного — FetchError.
    При ``allow_missing`` 404/410 дают None вместо исключения.
    """
    url = f"{BASE_URL}{path}"
    logger.info("GET %s", url)
    resp = client.get(url)
    if allow_missing and resp.status_code in (404, 410):
        return None
    resp.raise_for_status()
    final_path = urlsplit(str(resp.url)).path
    if _normalize_path(final_path) != _normalize_path(path):
        raise FetchError(f"redirected: {path} -> {final_path}")
    return resp.text


def fetch_html(path: str) -> str:
    """HTML страницы sinoptik.ua по пути вида "/pohoda/<slug>[/...]"."""
    if not path.startswith("/pohoda/"):
        # Защита: /api/ и прочее запрещены robots.txt — ходим только в /pohoda/.
        raise ValueError(f"Разрешены только пути /pohoda/...: {path!r}")
    with _client() as client:
        html = _get_checked(client, path)
    assert html is not None
    return html


def fetch_location_page(slug: str) -> str | None:
    """HTML /pohoda/<slug> или None, если такой локации нет (404/410/редирект)."""
    path = f"/pohoda/{slug}"
    with _client() as client:
        try:
            return _get_checked(client, path, allow_missing=True)
        except FetchError:
            return None


def fetch_week_html(city_slug: str = DEFAULT_CITY_SLUG) -> tuple[str, str]:
    """(html, url) страницы с 7-дневным таб-баром (та же, что "сегодня")."""
    path = f"/pohoda/{city_slug}"
    return fetch_html(path), f"{BASE_URL}{path}"


def fetch_10days_html(city_slug: str = DEFAULT_CITY_SLUG) -> tuple[str, str]:
    """(html, url) страницы с 10-дневным таб-баром."""
    path = f"/pohoda/{city_slug}/10-dniv"
    return fetch_html(path), f"{BASE_URL}{path}"


def fetch_today_html(city_slug: str = DEFAULT_CITY_SLUG) -> tuple[str, str]:
    """Возвращает (html, url) страницы прогноза на сегодня."""
    url = f"{BASE_URL}/pohoda/{city_slug}"
    with _client() as client:
        html = _get(client, url)
    return html, url


def fetch_tomorrow_html(city_slug: str = DEFAULT_CITY_SLUG, today_html: str | None = None) -> tuple[str, str]:
    """Возвращает (html, url) страницы прогноза на завтра.

    Завтрашний день определяется не хардкодом slug'а дня недели, а по
    положению в таб-баре дней на сегодняшней странице: первая вкладка —
    сегодня, вторая — завтра. Это устойчиво к смене недели/локализации.

    ``today_html`` позволяет переиспользовать уже скачанную (кешированную)
    сегодняшнюю страницу и сэкономить запрос.
    """
    today_url = f"{BASE_URL}/pohoda/{city_slug}"

    with _client() as client:
        if today_html is None:
            today_html = _get(client, today_url)
        tomorrow_url = extract_day_url(today_html, city_slug, 1)
        tomorrow_html = _get(client, tomorrow_url)

    return tomorrow_html, tomorrow_url


def _extract_tomorrow_url(today_html: str, city_slug: str) -> str:
    return extract_day_url(today_html, city_slug, 1)


def extract_day_url(page_html: str, city_slug: str, index: int) -> str:
    """Абсолютный URL дня по индексу вкладки в таб-баре (0 — сегодня, 1 — завтра...)."""
    soup = BeautifulSoup(page_html, "html.parser")

    day_tabs = [
        a
        for a in soup.find_all("a", attrs={"role": "menuitem", "aria-label": True})
        if DAY_TAB_RANGE_RE.search(a["aria-label"])
    ]

    if len(day_tabs) <= index:
        raise FetchError(
            f"Не удалось найти вкладки дней на странице {BASE_URL}/pohoda/{city_slug} "
            f"(найдено {len(day_tabs)}). Вероятно, изменилась вёрстка сайта."
        )

    href = day_tabs[index].get("href")
    if not href:
        raise FetchError(f"У вкладки дня #{index} нет href.")

    href = str(href)
    if href.startswith("http"):
        return href
    return f"{BASE_URL}{href}"
