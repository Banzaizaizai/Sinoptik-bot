from __future__ import annotations

import logging

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
MAX_MESSAGE_LENGTH = 4096


class TelegramSendError(RuntimeError):
    pass


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
)
def send_message(bot_token: str, chat_id: str, text: str) -> int:
    """Отправляет сообщение в Telegram, возвращает message_id."""
    if len(text) > MAX_MESSAGE_LENGTH:
        raise TelegramSendError(
            f"Сообщение длиной {len(text)} символов превышает лимит Telegram "
            f"({MAX_MESSAGE_LENGTH})."
        )

    url = f"{TELEGRAM_API}/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    with httpx.Client(timeout=15.0) as client:
        resp = client.post(url, json=payload)

    if resp.status_code != 200:
        raise TelegramSendError(f"Telegram API вернул {resp.status_code}: {resp.text}")

    data = resp.json()
    if not data.get("ok"):
        raise TelegramSendError(f"Telegram API ответил ok=false: {data}")

    logger.info("Сообщение отправлено в %s, message_id=%s", chat_id, data["result"]["message_id"])
    return data["result"]["message_id"]
