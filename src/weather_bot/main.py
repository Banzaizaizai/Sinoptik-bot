from __future__ import annotations

import logging
import logging.handlers
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Config
from .fetcher import FetchError, fetch_tomorrow_html
from .formatter import format_message
from .parser import ParseError, parse_tomorrow
from .telegram_sender import TelegramSendError, send_message

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"

logger = logging.getLogger("weather_bot")


def _setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "weather_bot.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    root.addHandler(stream_handler)


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


def run() -> int:
    _setup_logging()

    try:
        config = Config.load()
    except RuntimeError as exc:
        logger.error("Ошибка конфигурации: %s", exc)
        return 1

    tz = ZoneInfo(config.timezone)
    tomorrow_date = (datetime.now(tz) + timedelta(days=1)).date()

    try:
        html, url = fetch_tomorrow_html(config.city_slug)
        forecast = parse_tomorrow(
            html,
            source_url=url,
            city_name=config.city_name,
            forecast_date=tomorrow_date,
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

    logger.info("Прогноз на %s успешно отправлен", tomorrow_date)
    return 0


if __name__ == "__main__":
    sys.exit(run())
