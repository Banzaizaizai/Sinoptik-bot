"""Access control: the bot answers only whitelisted owners in private chats."""

from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes, filters

log = logging.getLogger(__name__)

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[Any]]


def owner_filter(chat_ids: Iterable[int]) -> filters.BaseFilter:
    """Filter for Command/Message handlers: allowed chat AND private chat."""
    return filters.Chat(chat_id=list(chat_ids)) & filters.ChatType.PRIVATE


def is_allowed(update: object, allowed: Iterable[int]) -> bool:
    """True if the update comes from an allowed private chat."""
    chat = getattr(update, "effective_chat", None)
    return chat is not None and chat.type == "private" and chat.id in set(allowed)


def allowed_ids(context: ContextTypes.DEFAULT_TYPE) -> tuple[int, ...]:
    return tuple(context.bot_data["cfg"].allowed_chat_ids)


def owner_only(func: Handler) -> Handler:
    """Guard for callback handlers: strangers get a silent ``answer()``."""

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Any:
        if not is_allowed(update, allowed_ids(context)):
            if update.callback_query is not None:
                await update.callback_query.answer()
            return None
        return await func(update, context)

    return wrapper


async def ignore_strangers(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catch-all (group 99): log foreign chats at INFO, never reply."""
    if not is_allowed(update, allowed_ids(context)):
        chat = getattr(update, "effective_chat", None)
        log.info("Ignored update from chat_id=%s", chat.id if chat else None)
