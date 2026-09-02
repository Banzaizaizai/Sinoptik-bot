"""Точка входа для запуска бота (в т.ч. из cron / Windows Task Scheduler).

Использование:
    .venv\\Scripts\\python.exe run_bot.py --mode today      # утренний запуск
    .venv\\Scripts\\python.exe run_bot.py --mode tomorrow   # вечерний запуск
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from weather_bot.main import run  # noqa: E402

if __name__ == "__main__":
    sys.exit(run())
