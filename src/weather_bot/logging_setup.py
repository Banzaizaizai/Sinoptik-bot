"""Общая настройка логирования: ротируемый файл в logs/ + stdout."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"


def setup_logging(logfile: str = "weather_bot.log") -> None:
    """Вешает на root-логгер файловый (logs/<logfile>) и консольный обработчики."""
    LOG_DIR.mkdir(exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / logfile, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    root.addHandler(stream_handler)
