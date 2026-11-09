r"""Entry point of the interactive (long-polling) bot; managed by systemd.

Usage:
    .venv\Scripts\python.exe run_interactive.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from weather_bot.bot.app import main  # noqa: E402

if __name__ == "__main__":
    main()
