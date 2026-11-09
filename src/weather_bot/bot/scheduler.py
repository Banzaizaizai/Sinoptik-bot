"""Subscription scheduling on PTB's JobQueue (APScheduler underneath).

* Every enabled subscription is one ``run_daily`` job named ``sub:<id>``.
* The database is the source of truth: ``rebuild_all`` recreates all jobs at
  startup, UI actions call ``schedule_sub`` / ``unschedule_sub`` after writes.
* ``send_log`` (PRIMARY KEY(subscription_id, fire_date)) deduplicates sends
  across double fires, catch-up, retries and DST oddities.

Weekday conventions: the DB stores ``days_mask`` with bit0=Mon..bit6=Sun;
PTB 22.x ``run_daily(days=...)`` uses 0=Sun..6=Sat (``JobQueue._CRON_MAPPING``
is ``("sun", "mon", ...)``). ``ptb_days`` converts; ``tests/test_scheduler.py``
pins this against the installed PTB.
"""

from __future__ import annotations

import asyncio
import html
import logging
import warnings
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from telegram.constants import ParseMode
from telegram.error import Forbidden, RetryAfter, TelegramError
from telegram.ext import ContextTypes, JobQueue

from weather_bot.bot import keyboards as kb
from weather_bot.bot import storage, texts
from weather_bot.bot.alerts import note_parse_error, note_success
from weather_bot.fetcher import FetchError
from weather_bot.formatter import split_message
from weather_bot.parser import ParseError

log = logging.getLogger(__name__)

Ctx = ContextTypes.DEFAULT_TYPE

MISFIRE_GRACE_SEC: Final = 900
CATCH_UP_WINDOW: Final = timedelta(minutes=15)
LATE_FIRE_SLACK: Final = timedelta(minutes=16)
RETRY_DELAY_SEC: Final = 300
JOB_KWARGS: Final[dict[str, Any]] = {
    "misfire_grace_time": MISFIRE_GRACE_SEC, "coalesce": True, "max_instances": 1,
}
_CRON_DAYS: Final = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")  # Mon-based


def job_name(sub_id: int) -> str:
    return f"sub:{sub_id}"


def ptb_days(mask: int) -> tuple[int, ...]:
    """DB mask (bit0=Mon..bit6=Sun) -> PTB ``days`` tuple (0=Sun..6=Sat)."""
    return tuple(sorted((b + 1) % 7 for b in range(7) if mask >> b & 1))


def _hm(time_local: str) -> tuple[int, int]:
    h, m = time_local.split(":")
    return int(h), int(m)


def now_kyiv(tz: ZoneInfo) -> datetime:
    """Current time in ``tz`` (module-level so tests can monkeypatch it)."""
    return datetime.now(tz)


# ---------------------------------------------------------------------------
# scheduling
# ---------------------------------------------------------------------------


def unschedule_sub(jq: JobQueue | None, sub_id: int) -> None:
    if jq is None:
        return
    for job in jq.get_jobs_by_name(job_name(sub_id)):
        job.schedule_removal()


def schedule_sub(jq: JobQueue | None, sub: storage.Subscription, tz: ZoneInfo) -> None:
    """(Re)create the daily job of ``sub``; disabled subscriptions just get removed."""
    if jq is None:
        return
    unschedule_sub(jq, sub.id)
    if not sub.enabled:
        return
    h, m = _hm(sub.time_local)
    jq.run_daily(
        fire, time=time(h, m, tzinfo=tz), days=ptb_days(sub.days_mask),
        name=job_name(sub.id), data={"id": sub.id}, chat_id=sub.chat_id,
        job_kwargs=dict(JOB_KWARGS),
    )


def rebuild_all(
    jq: JobQueue, db_path: str | Path, allowed: Iterable[int], tz: ZoneInfo
) -> int:
    """Drop every ``sub:*`` job and schedule all enabled subs of allowed chats."""
    allowed_set = set(allowed)
    for job in jq.jobs():
        if job.name and job.name.startswith("sub:"):
            job.schedule_removal()
    n = 0
    for sub in storage.list_enabled_subscriptions(db_path):
        if sub.chat_id in allowed_set:
            schedule_sub(jq, sub, tz)
            n += 1
    log.info("Scheduled %d subscription(s)", n)
    return n


def next_run(sub: storage.Subscription, tz: ZoneInfo, now: datetime) -> datetime | None:
    """Next fire time of ``sub`` after ``now`` (same cron rule as ``run_daily``)."""
    h, m = _hm(sub.time_local)
    trig = CronTrigger(
        day_of_week=",".join(_CRON_DAYS[b] for b in range(7) if sub.days_mask >> b & 1),
        hour=h, minute=m, second=0, timezone=tz,
    )
    return trig.get_next_fire_time(None, now)


def compute_fire_date(time_local: str, now: datetime) -> date:
    """Kyiv date a regular (non catch-up) fire belongs to.

    A job scheduled for 23:55 that runs late at 00:03 (within the misfire
    grace) still belongs to yesterday: if the scheduled wall time is *later*
    than the current wall time, take the date of ``now - 16 min``.
    """
    if time_local > now.strftime("%H:%M"):
        return (now - LATE_FIRE_SLACK).date()
    return now.date()


def due_catch_ups(
    subs: Iterable[storage.Subscription], db_path: str | Path, allowed: Iterable[int],
    now: datetime,
) -> list[tuple[storage.Subscription, date]]:
    """Enabled subs whose scheduled time was within the last 15 min and not finished.

    A send_log row still at ``'sending'`` also counts: it means the process died
    mid-send (or with a retry job pending, which does not survive a restart).
    """
    allowed_set = set(allowed)
    tz = now.tzinfo
    out: list[tuple[storage.Subscription, date]] = []
    for sub in subs:
        if not sub.enabled or sub.chat_id not in allowed_set:
            continue
        h, m = _hm(sub.time_local)
        # Today and yesterday: covers a 23:55 run missed by a restart at 00:05.
        for d in (now.date(), now.date() - timedelta(days=1)):
            if not sub.days_mask >> d.weekday() & 1:
                continue
            sched = datetime.combine(d, time(h, m), tzinfo=tz)
            if timedelta(0) <= now - sched <= CATCH_UP_WINDOW:
                entry = storage.get_send_log(db_path, sub.id, d)
                if entry is None or entry.status == "sending":
                    out.append((sub, d))
                break
    return out


def catch_up(
    jq: JobQueue, db_path: str | Path, allowed: Iterable[int], tz: ZoneInfo,
    now: datetime | None = None,
) -> list[int]:
    """Queue immediate fires for sends missed within the last 15 minutes."""
    now = now or now_kyiv(tz)
    due = due_catch_ups(storage.list_enabled_subscriptions(db_path), db_path, allowed, now)
    for sub, d in due:
        data: dict[str, Any] = {"id": sub.id, "fire_date": d.isoformat()}
        entry = storage.get_send_log(db_path, sub.id, d)
        if entry is not None:  # stuck at 'sending': resume it as a retry
            data["retry"] = True
        # catch_up runs before the scheduler starts; without a grace time
        # APScheduler's 1 s default would drop this job as a misfire.
        jq.run_once(
            fire, 0, data=data, name=f"catchup:{sub.id}", chat_id=sub.chat_id,
            job_kwargs=dict(JOB_KWARGS),
        )
        log.info("Catch-up queued for sub %d (%s)", sub.id, d)
    return [sub.id for sub, _ in due]


# ---------------------------------------------------------------------------
# firing
# ---------------------------------------------------------------------------


def _seconds(value: Any) -> float:
    return value.total_seconds() if isinstance(value, timedelta) else float(value)


async def _send(context: Ctx, chat_id: int, text: str, **kw: Any) -> None:
    """send_message with one retry after ``RetryAfter``."""
    try:
        await context.bot.send_message(chat_id, text, **kw)
    except RetryAfter as e:
        with warnings.catch_warnings():  # PTB 22.x deprecation: int -> timedelta
            warnings.simplefilter("ignore")
            delay = _seconds(e.retry_after)
        await asyncio.sleep(delay)
        await context.bot.send_message(chat_id, text, **kw)


async def _db(context: Ctx, fn: Any, *args: Any) -> Any:
    return await asyncio.to_thread(fn, context.bot_data["cfg"].db_path, *args)


async def _on_forbidden(context: Ctx, chat_id: int) -> None:
    """The user blocked the bot: disable and unschedule all their subscriptions."""
    ids = await _db(context, storage.disable_all_subscriptions, chat_id)
    for sid in ids:
        unschedule_sub(context.job_queue, sid)
    log.warning("Forbidden for chat %s: disabled %d subscription(s)", chat_id, len(ids))


async def fire(context: Ctx) -> None:
    """Job callback: send one subscription's forecast (deduplicated by send_log)."""
    job = context.job
    assert job is not None
    data: dict[str, Any] = job.data or {}  # type: ignore[assignment]
    cfg = context.bot_data["cfg"]
    tz = ZoneInfo(cfg.timezone)
    sid = int(data["id"])
    retry = bool(data.get("retry"))

    sub: storage.Subscription | None = await _db(context, storage.get_subscription, sid)
    if sub is None or not sub.enabled or sub.chat_id not in cfg.allowed_chat_ids:
        return
    fire_date = data.get("fire_date") or compute_fire_date(sub.time_local, now_kyiv(tz)).isoformat()

    if retry:
        entry = await _db(context, storage.get_send_log, sid, fire_date)
        if entry is None or entry.status != "sending":
            return
    elif not await _db(context, storage.try_start_send, sid, fire_date):
        log.info("sub %d for %s already handled; skipping", sid, fire_date)
        return

    cid = sub.chat_id
    if sub.location_id is not None:
        loc = await _db(context, storage.get_location, sub.location_id, cid)
    else:
        loc = await _db(context, storage.get_default_location, cid)
    try:
        if loc is None:
            await _send(context, cid, texts.SUB_NO_LOCATION.format(time=sub.time_local))
            await _db(context, storage.mark_send, sid, fire_date, "noloc")
            return

        try:
            text = await context.bot_data["service"].get_forecast(loc.slug, loc.title, sub.mode)
        except (FetchError, ParseError) as e:
            log.warning("sub %d fetch failed (retry=%s): %s", sid, retry, e)
            if isinstance(e, ParseError):
                await note_parse_error(context, e)
            if not retry and context.job_queue is not None:
                context.job_queue.run_once(
                    fire, RETRY_DELAY_SEC,
                    data={"id": sid, "fire_date": fire_date, "retry": True},
                    name=f"retry:{sid}", chat_id=cid, job_kwargs=dict(JOB_KWARGS),
                )
                return
            await _db(context, storage.mark_send, sid, fire_date, "failed", str(e)[:500])
            await _send(context, cid, texts.SUB_FETCH_FAILED.format(title=html.escape(loc.title)))
            return
        note_success(context)

        if sub.kind == "weekly":
            text = texts.WEEKLY_DIGEST_HEADER + "\n" + text
        parts = split_message(text)
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            await _send(
                context, cid, part, parse_mode=ParseMode.HTML, disable_web_page_preview=True,
                reply_markup=kb.forecast_footer(loc.id) if last else None,
            )
        await _db(context, storage.mark_send, sid, fire_date, "sent")
    except Forbidden as e:
        await _db(context, storage.mark_send, sid, fire_date, "failed", f"forbidden: {e}"[:500])
        await _on_forbidden(context, cid)
    except TelegramError as e:
        # Network/timeout/BadRequest/second RetryAfter: never leave the row at
        # 'sending' with nothing queued, or the day's send is silently lost.
        log.warning("sub %d send failed (retry=%s): %s", sid, retry, e)
        if not retry and context.job_queue is not None:
            context.job_queue.run_once(
                fire, RETRY_DELAY_SEC,
                data={"id": sid, "fire_date": fire_date, "retry": True},
                name=f"retry:{sid}", chat_id=cid, job_kwargs=dict(JOB_KWARGS),
            )
        else:
            await _db(context, storage.mark_send, sid, fire_date, "failed", str(e)[:500])
    except Exception as e:
        await _db(context, storage.mark_send, sid, fire_date, "failed", str(e)[:500])
        raise


__all__ = [
    "catch_up", "compute_fire_date", "due_catch_ups", "fire", "job_name", "next_run",
    "ptb_days", "rebuild_all", "schedule_sub", "unschedule_sub",
]
