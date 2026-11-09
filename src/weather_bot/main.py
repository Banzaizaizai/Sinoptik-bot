from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .config import Config
from .fetcher import FetchError, fetch_today_html, fetch_tomorrow_html
from .formatter import format_message
from .logging_setup import LOG_DIR, setup_logging  # noqa: F401  (LOG_DIR — для совместимости)
from .parser import ParseError, parse_forecast_day
from .telegram_sender import TelegramSendError, send_message

logger = logging.getLogger("weather_bot")

# Утренний запуск показывает прогноз на сегодня (актуален весь день до вечера),
# вечерний — прогноз на завтра. См. main.py:run(mode=...).
FETCHERS = {
    "today": fetch_today_html,
    "tomorrow": fetch_tomorrow_html,
}


def _target_date(mode: str, tz: ZoneInfo) -> date:
    today = datetime.now(tz).date()
    return today if mode == "today" else today + timedelta(days=1)


def _setup_logging() -> None:
    setup_logging("weather_bot.log")


def _notify_admin(config: Config, error: Exception) -> None:
    if not config.admin_chat_id:
        return
    try:
        send_message(
            config.telegram_bot_token,
            config.admin_chat_id,
            f"⚠️ Weather bot: сбой при подготовке прогноза.\n\n{type(error).__name__}: {error}",
        )
    except Exception:
        logger.exception("Не удалось отправить алерт админу")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Отправить прогноз погоды в Telegram")
    parser.add_argument(
        "--mode",
        choices=sorted(FETCHERS),
        default="tomorrow",
        help="'today' — прогноз на сегодня (утренний запуск), "
        "'tomorrow' — на завтра (вечерний запуск). По умолчанию: tomorrow.",
    )
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> int:
    _setup_logging()
    args = _parse_args(argv)

    try:
        config = Config.load()
    except RuntimeError as exc:
        logger.error("Ошибка конфигурации: %s", exc)
        return 1

    tz = ZoneInfo(config.timezone)
    target_date = _target_date(args.mode, tz)
    fetch_html = FETCHERS[args.mode]

    try:
        html, url = fetch_html(config.city_slug)
        forecast = parse_forecast_day(
            html,
            source_url=url,
            city_name=config.city_name,
            forecast_date=target_date,
        )
        message = format_message(forecast)
        send_message(config.telegram_bot_token, config.telegram_channel_id, message)
    except (FetchError, ParseError, TelegramSendError) as exc:
        logger.exception("Не удалось подготовить/отправить прогноз")
        _notify_admin(config, exc)
        return 1
    except Exception as exc:  # неожиданная ошибка — тоже не должна падать молча
        logger.exception("Непредвиденная ошибка")
        _notify_admin(config, exc)
        return 1

    logger.info("Прогноз (%s) на %s успешно отправлен", args.mode, target_date)
    return 0


if __name__ == "__main__":
    sys.exit(run())
