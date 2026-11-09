"""Throttled alerts to the primary owner (shared by handlers, error handler, scheduler)."""

from __future__ import annotations

import logging
import time

from telegram.ext import ContextTypes

from weather_bot.bot import texts

log = logging.getLogger(__name__)

ALERT_INTERVAL_SEC = 300.0
PARSE_ERROR_THRESHOLD = 3


async def alert_owner(context: ContextTypes.DEFAULT_TYPE, text: str, *, key: str = "error") -> bool:
    """Send ``text`` to the primary owner at most once per 5 min per ``key``."""
    last: dict[str, float] = context.bot_data.setdefault("alert_last", {})
    now = time.monotonic()
    if key in last and now - last[key] < ALERT_INTERVAL_SEC:
        return False
    last[key] = now
    try:
        await context.bot.send_message(context.bot_data["cfg"].owner_id, text)
    except Exception:  # an alert must never raise
        log.exception("Failed to alert owner")
        return False
    return True


async def note_parse_error(context: ContextTypes.DEFAULT_TYPE, error: BaseException) -> None:
    """Count consecutive ParseErrors; alert the owner from the 3rd one on."""
    n = context.bot_data.get("parse_errors", 0) + 1
    context.bot_data["parse_errors"] = n
    if n >= PARSE_ERROR_THRESHOLD:
        await alert_owner(
            context, texts.ALERT_PARSE.format(n=n, error=str(error)[:300]), key="parse"
        )


def note_success(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.bot_data["parse_errors"] = 0
