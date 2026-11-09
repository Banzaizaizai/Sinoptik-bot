from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    telegram_channel_id: str
    admin_chat_id: str | None
    city_slug: str
    city_name: str
    timezone: str

    @classmethod
    def load(cls) -> "Config":
        load_dotenv()

        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        channel_id = os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()
        if not token:
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN не задан. Скопируйте .env.example в .env и заполните его."
            )
        if not channel_id:
            raise RuntimeError(
                "TELEGRAM_CHANNEL_ID не задан. Скопируйте .env.example в .env и заполните его."
            )

        admin_chat_id = os.environ.get("ADMIN_CHAT_ID", "").strip() or None

        return cls(
            telegram_bot_token=token,
            telegram_channel_id=channel_id,
            admin_chat_id=admin_chat_id,
            city_slug=os.environ.get("CITY_SLUG", "drohobych").strip(),
            city_name=os.environ.get("CITY_NAME", "Дрогобич").strip(),
            timezone=os.environ.get("TIMEZONE", "Europe/Kyiv").strip(),
        )


# Project root (…/src/weather_bot/config.py -> repo root); relative DB paths
# resolve against it so the bot works regardless of the current directory.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _parse_chat_ids(raw: str) -> tuple[int, ...]:
    ids: list[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            raise RuntimeError(f"ALLOWED_CHAT_IDS: «{part}» не є числовим chat_id") from None
    # Preserve order (the first id is the "primary" owner for alerts/seeding), drop dups.
    return tuple(dict.fromkeys(ids))


@dataclass(frozen=True)
class BotConfig:
    """Settings of the interactive bot (run_interactive.py).

    Independent from :class:`Config`, so the bot does not need TELEGRAM_CHANNEL_ID.
    """

    telegram_bot_token: str
    allowed_chat_ids: tuple[int, ...]
    db_path: Path
    timezone: str = "Europe/Kyiv"
    cache_ttl_sec: int = 600

    @property
    def owner_id(self) -> int:
        """Primary owner: receives error alerts and the seeded default location."""
        return self.allowed_chat_ids[0]

    @classmethod
    def load(cls) -> "BotConfig":
        load_dotenv()

        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN не задан (см. .env.example).")
        ids = _parse_chat_ids(os.environ.get("ALLOWED_CHAT_IDS", ""))
        if not ids:
            raise RuntimeError("ALLOWED_CHAT_IDS не задан: укажите хотя бы один chat_id владельца.")

        db_raw = os.environ.get("BOT_DB_PATH", "").strip() or "data/bot.sqlite3"
        db_path = Path(db_raw)
        if not db_path.is_absolute():
            db_path = _PROJECT_ROOT / db_path

        ttl_raw = os.environ.get("CACHE_TTL_SEC", "").strip() or "600"
        try:
            ttl = int(ttl_raw)
        except ValueError:
            raise RuntimeError(f"CACHE_TTL_SEC должен быть целым числом, получено {ttl_raw!r}") from None

        return cls(
            telegram_bot_token=token,
            allowed_chat_ids=ids,
            db_path=db_path,
            timezone=os.environ.get("TIMEZONE", "Europe/Kyiv").strip() or "Europe/Kyiv",
            cache_ttl_sec=ttl,
        )
