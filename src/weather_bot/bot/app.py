"""Application wiring for the interactive bot: build, startup, error handling, main()."""

from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from telegram import BotCommand, BotCommandScopeChat, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from weather_bot.bot import handlers as h
from weather_bot.bot import scheduler, storage, texts
from weather_bot.bot.access import ignore_strangers, is_allowed, owner_filter
from weather_bot.bot.alerts import alert_owner
from weather_bot.config import BotConfig
from weather_bot.logging_setup import setup_logging
from weather_bot.service import ForecastService

log = logging.getLogger(__name__)

PRUNE_TIME = time(4, 10)  # daily send_log prune, Kyiv (outside the 03:xx DST hour)


def _kyiv_today(cfg: BotConfig) -> date:
    return datetime.now(ZoneInfo(cfg.timezone)).date()


async def _prune_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: BotConfig = context.bot_data["cfg"]
    n = await asyncio.to_thread(storage.prune_send_log, cfg.db_path, _kyiv_today(cfg))
    log.info("send_log pruned: %d rows", n)


async def post_init(app: Application) -> None:
    """Startup (design §10): migrate, upsert owners, seed, prune,
    rebuild subscription jobs, catch up sends missed in the last 15 min, commands."""
    cfg: BotConfig = app.bot_data["cfg"]
    await asyncio.to_thread(storage.migrate, cfg.db_path)
    for cid in cfg.allowed_chat_ids:
        await asyncio.to_thread(storage.upsert_user, cfg.db_path, cid)
    seeded = await asyncio.to_thread(storage.seed_default, cfg.db_path, cfg.owner_id)
    if seeded is not None:
        log.info("Seeded default location %s for owner %s", seeded.slug, cfg.owner_id)
    await asyncio.to_thread(storage.prune_send_log, cfg.db_path, _kyiv_today(cfg))

    if app.job_queue is not None:
        tz = ZoneInfo(cfg.timezone)
        app.job_queue.run_daily(_prune_job, time=PRUNE_TIME.replace(tzinfo=tz), name="prune_send_log")
        # Run on the loop thread: JobQueue/APScheduler calls are not made from
        # worker threads (the DB reads involved are tiny).
        scheduler.rebuild_all(app.job_queue, cfg.db_path, cfg.allowed_chat_ids, tz)
        scheduler.catch_up(app.job_queue, cfg.db_path, cfg.allowed_chat_ids, tz)
    else:
        log.warning("No JobQueue: subscriptions will not be sent")

    commands = [BotCommand(name, descr) for name, descr in texts.COMMANDS]
    for cid in cfg.allowed_chat_ids:
        try:
            await app.bot.set_my_commands(commands, scope=BotCommandScopeChat(cid))
        except TelegramError as e:  # e.g. the owner never opened the bot yet
            log.warning("set_my_commands for %s failed: %s", cid, e)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log, apologise to the owner chat, and alert the primary owner (throttled)."""
    err = context.error
    log.error("Unhandled error", exc_info=err)
    cfg: BotConfig = context.bot_data["cfg"]
    if isinstance(update, Update) and is_allowed(update, cfg.allowed_chat_ids):
        try:
            assert update.effective_chat is not None
            await context.bot.send_message(update.effective_chat.id, texts.ERROR_GENERIC)
        except Exception:
            log.debug("could not send error reply", exc_info=True)
    tb = "".join(traceback.format_exception_only(type(err), err)).strip() if err else "?"
    await alert_owner(context, texts.ALERT_ERROR.format(error=tb[:500]))


def build_application(cfg: BotConfig, *, service: ForecastService | None = None) -> Application:
    app = (
        ApplicationBuilder()
        .token(cfg.telegram_bot_token)
        .post_init(post_init)
        .build()
    )
    app.bot_data["cfg"] = cfg
    app.bot_data["service"] = service or ForecastService(ttl=cfg.cache_ttl_sec)

    owner = owner_filter(cfg.allowed_chat_ids)
    commands = {
        "start": h.cmd_start,
        "menu": h.cmd_start,
        "today": h.cmd_today,
        "tomorrow": h.cmd_tomorrow,
        "week": h.cmd_week,
        "10days": h.cmd_10days,
        "locations": h.cmd_locations,
        "subs": h.cmd_subs,
        "cancel": h.cmd_cancel,
    }
    for name, fn in commands.items():
        app.add_handler(CommandHandler(name, fn, filters=owner))
    app.add_handler(CallbackQueryHandler(h.on_callback))  # owner_only inside
    app.add_handler(MessageHandler(owner & filters.TEXT & ~filters.COMMAND, h.on_text))
    app.add_handler(TypeHandler(Update, ignore_strangers), group=99)
    app.add_error_handler(on_error)
    return app


def main() -> None:
    setup_logging("interactive.log")
    # httpx logs request URLs at INFO, and those contain the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = BotConfig.load()
    log.info("Starting interactive bot for %d owner(s)", len(cfg.allowed_chat_ids))
    app = build_application(cfg)
    app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=False)


__all__ = ["build_application", "main", "post_init", "on_error"]
