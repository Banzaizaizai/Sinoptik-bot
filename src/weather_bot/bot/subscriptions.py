"""Subscription UI: presets, weekly digest, custom-subscription wizard and card.

Registered into ``handlers.ROUTES`` / ``handlers.TEXT_STATES`` on import (the
bottom of ``handlers.py`` imports this module). Every DB write that changes
what should fire is followed by ``scheduler.schedule_sub`` / ``unschedule_sub``.

Wizard state lives in ``user_data["draft"]``:
``{"time": "HH:MM", "mode": str, "mask": int}``, filled step by step.
"""

from __future__ import annotations

import html
import re
from typing import Any
from zoneinfo import ZoneInfo

from telegram import Update

from weather_bot.bot import keyboards as kb
from weather_bot.bot import scheduler, storage, texts
from weather_bot.bot.callbacks import Callback
from weather_bot.bot.handlers import (
    AWAIT,
    DRAFT,
    Ctx,
    chat_id_of,
    clear_state,
    db,
    render_subscriptions,
    route,
    show,
    text_state,
    toast,
)

_TIME_RE = re.compile(r"^\s*([01]?\d|2[0-3])[:.\s]?([0-5]\d)\s*$")


# ---------------------------------------------------------------------------
# time input
# ---------------------------------------------------------------------------


def parse_time_input(text: str) -> str | None:
    """Normalize ``7:30`` / ``07.30`` / ``0730`` / ``7 30`` to ``HH:MM``; None if invalid."""
    m = _TIME_RE.match(text)
    if m is None:
        return None
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def is_dst_hour(time_local: str) -> bool:
    """03:00–03:59 is the DST switch hour in Kyiv; it is banned for subscriptions."""
    return time_local.startswith("03:")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _tz(context: Ctx) -> ZoneInfo:
    return ZoneInfo(context.bot_data["cfg"].timezone)


def _reschedule(context: Ctx, sub: storage.Subscription) -> None:
    scheduler.schedule_sub(context.job_queue, sub, _tz(context))


def _draft(context: Ctx) -> dict[str, Any]:
    assert context.user_data is not None
    d = context.user_data.get(DRAFT)
    return d if isinstance(d, dict) else {}


async def _location_label(context: Ctx, sub: storage.Subscription) -> str:
    if sub.location_id is None:
        return texts.DEFAULT_LOCATION_LABEL
    loc = await db(context, storage.get_location, sub.location_id, sub.chat_id)
    return loc.title if loc else "?"


def format_next_run(sub: storage.Subscription, tz: ZoneInfo) -> str:
    nxt = scheduler.next_run(sub, tz, scheduler.now_kyiv(tz))
    if nxt is None:
        return "—"
    return texts.NEXT_RUN.format(wd=texts.WEEKDAYS_LOWER[nxt.weekday()], d=nxt)


# ---------------------------------------------------------------------------
# presets
# ---------------------------------------------------------------------------


@route("Sp")
async def cb_toggle_preset(update: Update, context: Ctx, cb: Callback) -> None:
    preset = cb.str_arg()
    sub = await db(context, storage.toggle_preset, chat_id_of(update), preset)
    _reschedule(context, sub)
    tmpl = texts.SUB_TOGGLED_ON if sub.enabled else texts.SUB_TOGGLED_OFF
    await toast(update, tmpl.format(time=sub.time_local, mode=texts.MODE_PLAIN[sub.mode]))
    await render_subscriptions(update, context)


# ---------------------------------------------------------------------------
# weekly digest
# ---------------------------------------------------------------------------


async def render_weekly(update: Update, context: Ctx, sub: storage.Subscription) -> None:
    day = next((texts.WEEKDAYS_SHORT[b] for b in range(7) if sub.days_mask >> b & 1), "?")
    text = texts.WEEKLY_SCREEN.format(
        state=texts.WEEKLY_ON if sub.enabled else texts.WEEKLY_OFF, day=day, time=sub.time_local,
    )
    await show(update, text, kb.weekly_screen(sub))


@route("Sw")
async def cb_weekly(update: Update, context: Ctx, cb: Callback) -> None:
    sub = await db(context, storage.ensure_weekly, chat_id_of(update))
    await toast(update)
    await render_weekly(update, context, sub)


@route("Swo")
async def cb_weekly_toggle(update: Update, context: Ctx, cb: Callback) -> None:
    sub = await db(context, storage.toggle_weekly, chat_id_of(update))
    _reschedule(context, sub)
    await toast(update, texts.WEEKLY_ON.capitalize() if sub.enabled else texts.WEEKLY_OFF.capitalize())
    await render_weekly(update, context, sub)


@route("Swd")
async def cb_weekly_day(update: Update, context: Ctx, cb: Callback) -> None:
    sub = await db(context, storage.set_weekly_day, chat_id_of(update), cb.int_arg())
    _reschedule(context, sub)
    await toast(update)
    await render_weekly(update, context, sub)


@route("Swt")
async def cb_weekly_time(update: Update, context: Ctx, cb: Callback) -> None:
    t = cb.str_arg()
    if is_dst_hour(t):  # not offered by the keyboard; defensive
        await toast(update, texts.DST_HOUR)
        return
    sub = await db(context, storage.set_weekly_time, chat_id_of(update), t)
    _reschedule(context, sub)
    await toast(update)
    await render_weekly(update, context, sub)


# ---------------------------------------------------------------------------
# custom subscription wizard
# ---------------------------------------------------------------------------


@route("Sc")
async def cb_wizard_start(update: Update, context: Ctx, cb: Callback) -> None:
    assert context.user_data is not None
    subs = await db(context, storage.list_subscriptions, chat_id_of(update))
    if len(subs) >= storage.MAX_SUBSCRIPTIONS:
        await toast(update, texts.SUB_LIMIT.format(n=storage.MAX_SUBSCRIPTIONS))
        await render_subscriptions(update, context)
        return
    clear_state(context)
    context.user_data[DRAFT] = {}
    context.user_data[AWAIT] = "time"
    await toast(update)
    await show(update, texts.ASK_TIME, kb.wizard_time())


async def _accept_time(update: Update, context: Ctx, t: str) -> None:
    assert context.user_data is not None
    draft = _draft(context)
    draft["time"] = t
    context.user_data[DRAFT] = draft
    context.user_data.pop(AWAIT, None)
    await show(update, texts.ASK_MODE.format(time=t), kb.wizard_mode())


@text_state("time")
async def on_time_input(update: Update, context: Ctx, text: str) -> None:
    msg = update.effective_message
    assert msg is not None
    t = parse_time_input(text)
    if t is None:
        await msg.reply_text(texts.BAD_TIME, reply_markup=kb.wizard_time())
        return
    if is_dst_hour(t):
        await msg.reply_text(texts.DST_HOUR, reply_markup=kb.wizard_time())
        return
    await _accept_time(update, context, t)


@route("Sct")
async def cb_wizard_time(update: Update, context: Ctx, cb: Callback) -> None:
    t = cb.str_arg()
    if is_dst_hour(t):
        await toast(update, texts.DST_HOUR)
        return
    await toast(update)
    await _accept_time(update, context, t)


async def _stale(update: Update, context: Ctx) -> None:
    clear_state(context)
    await toast(update, texts.SUB_DRAFT_STALE)
    await render_subscriptions(update, context)


@route("Scm")
async def cb_wizard_mode(update: Update, context: Ctx, cb: Callback) -> None:
    draft = _draft(context)
    if "time" not in draft:
        await _stale(update, context)
        return
    draft["mode"] = cb.str_arg()
    await toast(update)
    await show(
        update, texts.ASK_DAYS.format(time=draft["time"], mode=texts.MODE_PLAIN[draft["mode"]]),
        kb.wizard_days(),
    )


@route("Scd")
async def cb_wizard_days(update: Update, context: Ctx, cb: Callback) -> None:
    draft = _draft(context)
    if "time" not in draft or "mode" not in draft:
        await _stale(update, context)
        return
    draft["mask"] = cb.int_arg()
    cid = chat_id_of(update)
    locs = await db(context, storage.list_locations, cid)
    if len(locs) < 2:
        await _finish(update, context, location_id=None)
        return
    user = await db(context, storage.get_user, cid)
    await toast(update)
    await show(
        update,
        texts.ASK_SUB_LOCATION.format(
            time=draft["time"], mode=texts.MODE_PLAIN[draft["mode"]],
            days=texts.days_label(draft["mask"]),
        ),
        kb.wizard_location(locs, user.default_location_id if user else None),
    )


@route("Scl")
async def cb_wizard_location(update: Update, context: Ctx, cb: Callback) -> None:
    draft = _draft(context)
    if not {"time", "mode", "mask"} <= draft.keys():
        await _stale(update, context)
        return
    loc_id = cb.int_arg()
    await _finish(update, context, location_id=loc_id or None)


async def _finish(update: Update, context: Ctx, *, location_id: int | None) -> None:
    """Validate, create, schedule and confirm the drafted subscription."""
    draft = _draft(context)
    cid = chat_id_of(update)
    try:
        sub = await db(
            context, storage.create_custom, cid, time_local=draft["time"], mode=draft["mode"],
            days_mask=draft["mask"], location_id=location_id,
        )
    except storage.DuplicateError:
        clear_state(context)
        await toast(update, texts.SUB_DUPLICATE)
        await render_subscriptions(update, context, texts.SUB_DUPLICATE + "\n\n")
        return
    except storage.LimitReached:
        clear_state(context)
        msg = texts.SUB_LIMIT.format(n=storage.MAX_SUBSCRIPTIONS)
        await toast(update, msg)
        await render_subscriptions(update, context, msg + "\n\n")
        return
    except storage.NotFound:
        await _stale(update, context)
        return
    clear_state(context)
    _reschedule(context, sub)
    label = await _location_label(context, sub)
    confirm = texts.SUB_CREATED.format(
        time=sub.time_local, mode=texts.MODE_PLAIN[sub.mode], location=html.escape(label),
        days=texts.days_label(sub.days_mask), next=format_next_run(sub, _tz(context)),
    )
    await toast(update)
    await render_subscriptions(update, context, confirm + "\n\n")


# ---------------------------------------------------------------------------
# subscription card
# ---------------------------------------------------------------------------


async def _own_sub(update: Update, context: Ctx, sub_id: int) -> storage.Subscription | None:
    sub = await db(context, storage.get_subscription, sub_id, chat_id_of(update))
    if sub is None:
        await toast(update, texts.NOT_FOUND)
        await render_subscriptions(update, context)
    return sub


async def render_sub_card(update: Update, context: Ctx, sub: storage.Subscription) -> None:
    label = await _location_label(context, sub)
    text = texts.SUB_CARD.format(
        mark="🔔" if sub.enabled else "🔕", time=sub.time_local, mode=texts.MODE_PLAIN[sub.mode],
        location=html.escape(label), days=texts.days_label(sub.days_mask),
        state=texts.SUB_STATE_ON if sub.enabled else texts.SUB_STATE_OFF,
    )
    await show(update, text, kb.sub_card(sub))


@route("Sv")
async def cb_sub_card(update: Update, context: Ctx, cb: Callback) -> None:
    sub = await _own_sub(update, context, cb.int_arg())
    if sub is None:
        return
    await toast(update)
    await render_sub_card(update, context, sub)


@route("Se")
async def cb_sub_toggle(update: Update, context: Ctx, cb: Callback) -> None:
    sub = await _own_sub(update, context, cb.int_arg())
    if sub is None:
        return
    sub = await db(context, storage.set_subscription_enabled, sub.chat_id, sub.id, not sub.enabled)
    _reschedule(context, sub)
    tmpl = texts.SUB_TOGGLED_ON if sub.enabled else texts.SUB_TOGGLED_OFF
    await toast(update, tmpl.format(time=sub.time_local, mode=texts.MODE_PLAIN[sub.mode]))
    await render_sub_card(update, context, sub)


@route("Sx")
async def cb_sub_delete_ask(update: Update, context: Ctx, cb: Callback) -> None:
    sub = await _own_sub(update, context, cb.int_arg())
    if sub is None:
        return
    label = await _location_label(context, sub)
    await toast(update)
    await show(
        update,
        texts.SUB_DELETE_CONFIRM.format(
            time=sub.time_local, mode=texts.MODE_PLAIN[sub.mode], location=html.escape(label)
        ),
        kb.sub_delete_confirm(sub.id),
    )


@route("Sxy")
async def cb_sub_delete(update: Update, context: Ctx, cb: Callback) -> None:
    sub = await _own_sub(update, context, cb.int_arg())
    if sub is None:
        return
    await db(context, storage.delete_subscription, sub.chat_id, sub.id)
    scheduler.unschedule_sub(context.job_queue, sub.id)
    await toast(update, texts.SUB_DELETED)
    await render_subscriptions(update, context)
