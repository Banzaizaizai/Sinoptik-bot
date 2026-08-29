from __future__ import annotations

import os
from dataclasses import dataclass

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
