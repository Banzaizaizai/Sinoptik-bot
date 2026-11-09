"""Command handlers, the callback router and text-input states.

Extension points (used by the scheduler/subscriptions step):

* ``@route("Verb")`` registers an async ``(update, context, cb)`` callback
  handler in ``ROUTES``; a later registration for the same verb replaces the
  earlier one. ``bot/subscriptions.py`` (imported at the bottom of this
  module) registers the ``S*`` verbs this way.
* ``@text_state("name")`` registers a handler for free-text input while
  ``user_data["await"] == "name"``.
* ``bot_data`` keys: ``cfg`` (BotConfig), ``service`` (ForecastService),
  ``resolve`` (optional override of ``locations.resolve_candidates``).
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from weather_bot.bot import keyboards as kb
from weather_bot.bot import scheduler, storage, texts
from weather_bot.bot.access import owner_only
from weather_bot.bot.alerts import note_parse_error, note_success
from weather_bot.bot.callbacks import Callback, decode
from weather_bot.fetcher import FetchError
from weather_bot.formatter import split_message
from weather_bot.locations import Candidate, GeocoderError, resolve_candidates
from weather_bot.parser import ParseError

log = logging.getLogger(__name__)

Ctx = ContextTypes.DEFAULT_TYPE
CallbackHandler = Callable[[Update, Ctx, Callback], Awaitable[None]]
TextHandler = Callable[[Update, Ctx, str], Awaitable[None]]
T = TypeVar("T")

ROUTES: dict[str, CallbackHandler] = {}
TEXT_STATES: dict[str, TextHandler] = {}

# user_data keys
AWAIT = "await"
CANDS = "cands"
DRAFT = "draft"

_LOC_NAME_RE = re.compile(r"[^\W\d_](?:[^\W\d_]|[ \-'ʼ’])*", re.UNICODE)
_SLUG_RE = re.compile(r"^[a-z0-9-]+$")


def route(*verbs: str) -> Callable[[CallbackHandler], CallbackHandler]:
    def deco(fn: CallbackHandler) -> CallbackHandler:
        for v in verbs:
            ROUTES[v] = fn
        return fn
    return deco


def text_state(name: str) -> Callable[[TextHandler], TextHandler]:
    def deco(fn: TextHandler) -> TextHandler:
        TEXT_STATES[name] = fn
        return fn
    return deco


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def db(context: Ctx, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run a sync storage function in a thread with the configured DB path."""
    return await asyncio.to_thread(fn, context.bot_data["cfg"].db_path, *args, **kwargs)


def chat_id_of(update: Update) -> int:
    assert update.effective_chat is not None
    return update.effective_chat.id


def clear_state(context: Ctx) -> None:
    ud = context.user_data
    if ud is not None:
        for key in (AWAIT, CANDS, DRAFT):
            ud.pop(key, None)


async def show(update: Update, text: str, markup: InlineKeyboardMarkup | None) -> None:
    """Edit the menu message in place (callbacks) or send a new one (commands/text)."""
    q = update.callback_query
    if q is not None and q.message is not None:
        try:
            await q.edit_message_text(
                text, reply_markup=markup, parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            return
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return
            log.info("edit failed (%s); sending a new message", e)
    msg = update.effective_message
    assert msg is not None
    await msg.reply_text(
        text, reply_markup=markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )


async def toast(update: Update, text: str | None = None) -> None:
    """Answer the callback query (safe to call more than once)."""
    q = update.callback_query
    if q is None:
        return
    try:
        await q.answer(text)
    except BadRequest:  # already answered / too old
        pass


# ---------------------------------------------------------------------------
# screens
# ---------------------------------------------------------------------------


async def menu_view(context: Ctx, cid: int) -> tuple[str, InlineKeyboardMarkup]:
    default = await db(context, storage.get_default_location, cid)
    locs = await db(context, storage.list_locations, cid)
    favs = await db(context, storage.list_favorites, cid, kb.MAX_MENU_FAVORITES)
    text = (
        texts.MENU_HEADER.format(title=html.escape(default.title)) if default
        else texts.MENU_NO_LOCATION
    )
    return text, kb.main_menu(location_count=len(locs), favorites=favs)


async def render_menu(update: Update, context: Ctx) -> None:
    await show(update, *(await menu_view(context, chat_id_of(update))))


async def render_locations(update: Update, context: Ctx, prefix: str = "") -> None:
    cid = chat_id_of(update)
    locs = await db(context, storage.list_locations, cid)
    user = await db(context, storage.get_user, cid)
    default_id = user.default_location_id if user else None
    text = texts.LOCATIONS_HEADER if locs else texts.LOCATIONS_EMPTY
    await show(update, prefix + text, kb.locations_list(locs, default_id))


async def render_location_card(
    update: Update, context: Ctx, loc_id: int, prefix: str = ""
) -> None:
    cid = chat_id_of(update)
    loc = await db(context, storage.get_location, loc_id, cid)
    if loc is None:
        await toast(update, texts.NOT_FOUND)
        await render_locations(update, context)
        return
    user = await db(context, storage.get_user, cid)
    is_default = user is not None and user.default_location_id == loc.id
    text = texts.LOCATION_CARD.format(
        title=html.escape(loc.title), description=html.escape(loc.description or "")
    ).rstrip()
    if is_default:
        text += "\n" + texts.LOCATION_CARD_DEFAULT_NOTE
    await show(update, prefix + text, kb.location_card(loc, is_default=is_default))


async def render_subscriptions(update: Update, context: Ctx, prefix: str = "") -> None:
    """Subscriptions overview; actions live in ``bot/subscriptions.py``."""
    cid = chat_id_of(update)
    presets = {p: await db(context, storage.get_preset, cid, p) for p in storage.PRESETS}
    weekly = await db(context, storage.get_weekly, cid)
    customs: list[tuple[storage.Subscription, str]] = []
    for sub in await db(context, storage.list_subscriptions, cid):
        if sub.kind != "custom":
            continue
        label = texts.DEFAULT_LOCATION_LABEL
        if sub.location_id is not None:
            loc = await db(context, storage.get_location, sub.location_id, cid)
            label = loc.title if loc else "?"
        customs.append((sub, label))
    await show(update, prefix + texts.SUBS_HEADER, kb.subscriptions(presets, weekly, customs))


async def prompt_add_location(update: Update, context: Ctx, prefix: str = "") -> None:
    assert context.user_data is not None
    context.user_data.pop(CANDS, None)
    context.user_data[AWAIT] = "loc_query"
    await show(update, prefix + texts.ASK_LOCATION, kb.cancel_only())


# ---------------------------------------------------------------------------
# forecasts
# ---------------------------------------------------------------------------


async def send_forecast(
    update: Update, context: Ctx, *, slug: str, title: str, mode: str, loc_id: int | None
) -> None:
    """Fetch and send a forecast as new message(s) with the period-switch footer."""
    cid = chat_id_of(update)
    try:
        await context.bot.send_chat_action(cid, ChatAction.TYPING)
    except Exception:  # cosmetic only
        log.debug("send_chat_action failed", exc_info=True)
    try:
        text = await context.bot_data["service"].get_forecast(slug, title, mode)
    except (FetchError, ParseError) as e:
        log.warning("forecast %s/%s failed: %s", slug, mode, e)
        if isinstance(e, ParseError):
            await note_parse_error(context, e)
        await context.bot.send_message(
            cid, texts.FORECAST_FAILED.format(title=html.escape(title)),
            reply_markup=kb.forecast_footer(loc_id),
        )
        return
    note_success(context)
    parts = split_message(text)
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        await context.bot.send_message(
            cid, part, parse_mode=ParseMode.HTML, disable_web_page_preview=True,
            reply_markup=kb.forecast_footer(loc_id) if last else None,
        )


@route("f")
async def cb_forecast(update: Update, context: Ctx, cb: Callback) -> None:
    mode, loc_id = cb.str_arg(0), cb.int_arg(1)
    cid = chat_id_of(update)
    if loc_id == 0:
        loc = await db(context, storage.get_default_location, cid)
        if loc is None:
            await toast(update, texts.NEED_LOCATION_FIRST)
            await prompt_add_location(update, context)
            return
    else:
        loc = await db(context, storage.get_location, loc_id, cid)
        if loc is None:
            await toast(update, texts.NOT_FOUND)
            await render_menu(update, context)
            return
    await toast(update, texts.LOADING)
    await send_forecast(update, context, slug=loc.slug, title=loc.title, mode=mode, loc_id=loc.id)


@route("fl")
async def cb_forecast_pick(update: Update, context: Ctx, cb: Callback) -> None:
    mode = cb.str_arg(0)
    cid = chat_id_of(update)
    locs = await db(context, storage.list_locations, cid)
    user = await db(context, storage.get_user, cid)
    await toast(update)
    await show(
        update, texts.PICK_LOCATION_FOR.format(mode=texts.MODE_PLAIN[mode]),
        kb.location_picker(mode, locs, user.default_location_id if user else None),
    )


async def _forecast_command(update: Update, context: Ctx, mode: str) -> None:
    clear_state(context)
    cid = chat_id_of(update)
    query = " ".join(context.args or []).strip()
    if not query:
        loc = await db(context, storage.get_default_location, cid)
        if loc is None:
            await prompt_add_location(update, context, texts.NEED_LOCATION_FIRST + "\n")
            return
        await send_forecast(update, context, slug=loc.slug, title=loc.title, mode=mode, loc_id=loc.id)
        return
    saved = await db(context, storage.find_location_by_title, cid, query)
    if saved is not None:
        await send_forecast(
            update, context, slug=saved.slug, title=saved.title, mode=mode, loc_id=saved.id
        )
        return
    # Not saved: resolve on the fly and use the first hit without saving it.
    msg = update.effective_message
    assert msg is not None
    try:
        cands = await _resolve(context, query)
    except GeocoderError:
        await msg.reply_text(texts.SEARCH_UNAVAILABLE)
        return
    if not cands:
        await msg.reply_text(texts.LOCATION_ARG_NOT_FOUND.format(q=query))
        return
    c = cands[0]
    await send_forecast(update, context, slug=c.slug, title=c.title, mode=mode, loc_id=None)


async def cmd_today(update: Update, context: Ctx) -> None:
    await _forecast_command(update, context, "today")


async def cmd_tomorrow(update: Update, context: Ctx) -> None:
    await _forecast_command(update, context, "tomorrow")


async def cmd_week(update: Update, context: Ctx) -> None:
    await _forecast_command(update, context, "week")


async def cmd_10days(update: Update, context: Ctx) -> None:
    await _forecast_command(update, context, "10days")


# ---------------------------------------------------------------------------
# menu / cancel / commands
# ---------------------------------------------------------------------------


@route("m")
async def cb_menu(update: Update, context: Ctx, cb: Callback) -> None:
    await toast(update)
    await render_menu(update, context)


@route("x")
async def cb_cancel(update: Update, context: Ctx, cb: Callback) -> None:
    clear_state(context)
    await toast(update, texts.CANCELLED)
    await render_menu(update, context)


async def cmd_start(update: Update, context: Ctx) -> None:
    clear_state(context)
    # Attach the persistent "🏠 Меню" reply-keyboard button (needs its own message).
    await context.bot.send_message(
        chat_id_of(update), texts.KEYBOARD_READY, reply_markup=kb.persistent_menu()
    )
    await render_menu(update, context)


async def cmd_cancel(update: Update, context: Ctx) -> None:
    clear_state(context)
    await render_menu(update, context)


async def cmd_locations(update: Update, context: Ctx) -> None:
    clear_state(context)
    await render_locations(update, context)


async def cmd_subs(update: Update, context: Ctx) -> None:
    clear_state(context)
    await render_subscriptions(update, context)


# ---------------------------------------------------------------------------
# locations
# ---------------------------------------------------------------------------


@route("L")
async def cb_locations(update: Update, context: Ctx, cb: Callback) -> None:
    await toast(update)
    await render_locations(update, context)


@route("Lv")
async def cb_location_card(update: Update, context: Ctx, cb: Callback) -> None:
    await toast(update)
    await render_location_card(update, context, cb.int_arg())


@route("Ld")
async def cb_make_default(update: Update, context: Ctx, cb: Callback) -> None:
    cid, loc_id = chat_id_of(update), cb.int_arg()
    loc = await db(context, storage.get_location, loc_id, cid)
    if loc is None:
        await toast(update, texts.NOT_FOUND)
        await render_locations(update, context)
        return
    await db(context, storage.set_default, cid, loc_id)
    await toast(update, texts.DEFAULT_SET.format(title=loc.title))
    await render_location_card(update, context, loc_id)


@route("Lf")
async def cb_toggle_favorite(update: Update, context: Ctx, cb: Callback) -> None:
    cid, loc_id = chat_id_of(update), cb.int_arg()
    try:
        fav = await db(context, storage.toggle_favorite, cid, loc_id)
    except storage.NotFound:
        await toast(update, texts.NOT_FOUND)
        await render_locations(update, context)
        return
    await toast(update, texts.FAV_ADDED if fav else texts.FAV_REMOVED)
    await render_location_card(update, context, loc_id)


@route("Lr")
async def cb_delete_ask(update: Update, context: Ctx, cb: Callback) -> None:
    cid, loc_id = chat_id_of(update), cb.int_arg()
    loc = await db(context, storage.get_location, loc_id, cid)
    if loc is None:
        await toast(update, texts.NOT_FOUND)
        await render_locations(update, context)
        return
    n = await db(context, storage.count_location_subscriptions, loc_id)
    await toast(update)
    await show(
        update, texts.DELETE_CONFIRM.format(title=html.escape(loc.title), n=n),
        kb.delete_confirm(loc_id),
    )


def unschedule(context: Ctx, sub_ids: list[int]) -> None:
    """Remove JobQueue jobs of deleted subscriptions (job name ``sub:<id>``)."""
    for sid in sub_ids:
        scheduler.unschedule_sub(context.job_queue, sid)


@route("Lry")
async def cb_delete_confirm(update: Update, context: Ctx, cb: Callback) -> None:
    cid, loc_id = chat_id_of(update), cb.int_arg()
    loc = await db(context, storage.get_location, loc_id, cid)
    if loc is None:
        await toast(update, texts.NOT_FOUND)
        await render_locations(update, context)
        return
    sub_ids = await db(context, storage.delete_location, cid, loc_id)
    unschedule(context, sub_ids)
    await toast(update, texts.DELETED.format(title=loc.title))
    await render_locations(update, context)


@route("La")
async def cb_add_location(update: Update, context: Ctx, cb: Callback) -> None:
    await toast(update)
    await prompt_add_location(update, context)


async def _resolve(context: Ctx, query: str) -> list[Candidate]:
    resolver = context.bot_data.get("resolve", resolve_candidates)
    return await asyncio.to_thread(resolver, query)


def valid_location_query(q: str) -> bool:
    """2–60 letters/spaces/-'ʼ’, or a raw sinoptik slug."""
    if _SLUG_RE.match(q):
        return True
    return 2 <= len(q) <= 60 and _LOC_NAME_RE.fullmatch(q) is not None


@text_state("loc_query")
async def on_location_query(update: Update, context: Ctx, text: str) -> None:
    assert context.user_data is not None
    msg = update.effective_message
    assert msg is not None
    q = " ".join(text.split())
    if not valid_location_query(q):
        await msg.reply_text(texts.BAD_LOCATION_INPUT, reply_markup=kb.cancel_only())
        return
    status = await msg.reply_text(texts.SEARCHING)
    try:
        cands = await _resolve(context, q)
    except GeocoderError:
        await status.edit_text(texts.SEARCH_UNAVAILABLE, reply_markup=kb.cancel_only())
        return
    if not cands:  # keep waiting for another name
        await status.edit_text(
            texts.SEARCH_NOT_FOUND.format(q=html.escape(q)), reply_markup=kb.cancel_only(),
            parse_mode=ParseMode.HTML,
        )
        return

    context.user_data.pop(AWAIT, None)
    cands = cands[:5]
    context.user_data[CANDS] = [
        {"slug": c.slug, "title": c.title, "description": c.description, "lat": c.lat, "lng": c.lng}
        for c in cands
    ]
    if len(cands) == 1:
        c = cands[0]
        await status.edit_text(
            texts.FOUND_ONE.format(title=html.escape(c.title), description=html.escape(c.description)),
            reply_markup=kb.single_candidate(), parse_mode=ParseMode.HTML,
        )
        return
    lines = [texts.FOUND_MANY] + [
        texts.FOUND_MANY_LINE.format(
            n=i + 1, title=html.escape(c.title), description=html.escape(c.description)
        )
        for i, c in enumerate(cands)
    ]
    await status.edit_text(
        "\n".join(lines), parse_mode=ParseMode.HTML,
        reply_markup=kb.candidates([(c.title, c.description) for c in cands]),
    )


@route("Lc", "Lcd")
async def cb_pick_candidate(update: Update, context: Ctx, cb: Callback) -> None:
    assert context.user_data is not None
    idx = cb.int_arg()
    cands: list[dict[str, Any]] = context.user_data.get(CANDS) or []
    if idx >= len(cands):
        await toast(update, texts.SEARCH_STALE)
        await prompt_add_location(update, context, texts.SEARCH_STALE + "\n")
        return
    c = cands[idx]
    cid = chat_id_of(update)
    try:
        loc = await db(
            context, storage.add_location, cid, c["title"], c["slug"],
            description=c["description"] or None, lat=c["lat"], lng=c["lng"],
            make_default=cb.verb == "Lcd",
        )
    except storage.DuplicateError:
        await toast(update, texts.ALREADY_EXISTS.format(title=c["title"]))
        await render_locations(update, context, texts.ALREADY_EXISTS.format(title=html.escape(c["title"])) + "\n\n")
        return
    except storage.LimitReached:
        await toast(update, texts.LOCATIONS_LIMIT.format(n=storage.MAX_LOCATIONS))
        await render_locations(update, context)
        return
    clear_state(context)
    await toast(update)
    await render_location_card(
        update, context, loc.id, texts.ADDED.format(title=html.escape(loc.title)) + "\n\n"
    )


# ---------------------------------------------------------------------------
# subscriptions: the "S" screen (other S* verbs live in bot/subscriptions.py)
# ---------------------------------------------------------------------------


@route("S")
async def cb_subscriptions(update: Update, context: Ctx, cb: Callback) -> None:
    await toast(update)
    await render_subscriptions(update, context)


# ---------------------------------------------------------------------------
# dispatchers
# ---------------------------------------------------------------------------


@owner_only
async def on_callback(update: Update, context: Ctx) -> None:
    """Single CallbackQueryHandler: decode and dispatch through ROUTES."""
    q = update.callback_query
    assert q is not None
    try:
        cb = decode(q.data)
        handler = ROUTES[cb.verb]
    except (ValueError, KeyError):
        await toast(update, texts.STALE_MENU)
        await render_menu_new(update, context)
        return
    # Contract: every routed handler answers the query itself (via ``toast``).
    await handler(update, context, cb)


async def render_menu_new(update: Update, context: Ctx) -> None:
    """Send the main menu as a new message (stale callbacks)."""
    text, markup = await menu_view(context, chat_id_of(update))
    await context.bot.send_message(
        chat_id_of(update), text, parse_mode=ParseMode.HTML, reply_markup=markup
    )


async def on_text(update: Update, context: Ctx) -> None:
    """Free text: dispatch by ``user_data['await']``; otherwise show the menu."""
    msg = update.effective_message
    if msg is None or msg.text is None:
        return
    if msg.text.strip() == texts.MENU_BUTTON:
        # The persistent button always wins over any pending text-input state.
        clear_state(context)
        await render_menu(update, context)
        return
    state = (context.user_data or {}).get(AWAIT)
    handler = TEXT_STATES.get(state) if state else None
    if handler is None:
        await render_menu(update, context)
        return
    await handler(update, context, msg.text)


# Registers the S* callback routes and the "time" text state. Imported last
# because it depends on the helpers above (no import cycle at module load).
from weather_bot.bot import subscriptions as _subscriptions  # noqa: E402,F401
