"""InlineKeyboardMarkup builders. Pure functions of storage rows -> markup."""

from __future__ import annotations

from collections.abc import Sequence

from telegram import InlineKeyboardButton as Btn
from telegram import InlineKeyboardMarkup

from weather_bot.bot import texts
from weather_bot.bot.callbacks import encode
from weather_bot.bot.storage import (
    PRESETS,
    WEEKLY_DEFAULT_MASK,
    WEEKLY_DEFAULT_TIME,
    Location,
    Subscription,
)

MODES: tuple[str, ...] = ("today", "tomorrow", "week", "10days")
MAX_MENU_FAVORITES = 4
_CAND_DESCR_MAX = 40


def _forecast_grid(loc_id: int) -> list[list[Btn]]:
    """2x2 grid of forecast buttons for a location (0 = default)."""
    b = [Btn(texts.MODE_BUTTONS[m], callback_data=encode("f", m, loc_id)) for m in MODES]
    return [b[:2], b[2:]]


def main_menu(
    *, location_count: int, favorites: Sequence[Location]
) -> InlineKeyboardMarkup:
    rows = _forecast_grid(0)
    if location_count >= 2:
        rows.append([Btn(texts.BTN_OTHER_LOCATION, callback_data=encode("fl", "today"))])
    fav_buttons = [
        Btn(texts.BTN_FAVORITE_QUICK.format(title=f.title), callback_data=encode("Lv", f.id))
        for f in favorites[:MAX_MENU_FAVORITES]
    ]
    rows.extend(fav_buttons[i:i + 2] for i in range(0, len(fav_buttons), 2))
    rows.append([
        Btn(texts.BTN_LOCATIONS, callback_data=encode("L")),
        Btn(texts.BTN_SUBS, callback_data=encode("S")),
    ])
    return InlineKeyboardMarkup(rows)


def forecast_footer(loc_id: int | None) -> InlineKeyboardMarkup:
    """Period-switch row under a forecast (+ menu). ``None`` = unsaved location."""
    rows: list[list[Btn]] = []
    if loc_id is not None:
        rows.append([
            Btn(texts.MODE_BUTTONS[m], callback_data=encode("f", m, loc_id)) for m in MODES
        ])
    rows.append([Btn(texts.BTN_MENU, callback_data=encode("m"))])
    return InlineKeyboardMarkup(rows)


def location_label(loc: Location, default_id: int | None) -> str:
    marks = ""
    if loc.id == default_id:
        marks += texts.MARK_DEFAULT
    if loc.is_favorite:
        marks += texts.MARK_FAVORITE
    return f"{marks} {loc.title}" if marks else loc.title


def locations_list(locs: Sequence[Location], default_id: int | None) -> InlineKeyboardMarkup:
    rows = [[Btn(location_label(l, default_id), callback_data=encode("Lv", l.id))] for l in locs]
    rows.append([
        Btn(texts.BTN_ADD_LOCATION, callback_data=encode("La")),
        Btn(texts.BTN_MENU, callback_data=encode("m")),
    ])
    return InlineKeyboardMarkup(rows)


def location_card(loc: Location, *, is_default: bool) -> InlineKeyboardMarkup:
    rows = _forecast_grid(loc.id)
    if not is_default:
        rows.append([Btn(texts.BTN_MAKE_DEFAULT, callback_data=encode("Ld", loc.id))])
    fav_text = texts.BTN_FAV_REMOVE if loc.is_favorite else texts.BTN_FAV_ADD
    rows.append([Btn(fav_text, callback_data=encode("Lf", loc.id))])
    rows.append([Btn(texts.BTN_DELETE, callback_data=encode("Lr", loc.id))])
    rows.append([Btn(texts.BTN_BACK, callback_data=encode("L"))])
    return InlineKeyboardMarkup(rows)


def delete_confirm(loc_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        Btn(texts.BTN_DELETE_YES, callback_data=encode("Lry", loc_id)),
        Btn(texts.BTN_CANCEL_PLAIN, callback_data=encode("Lv", loc_id)),
    ]])


def location_picker(mode: str, locs: Sequence[Location], default_id: int | None) -> InlineKeyboardMarkup:
    """"Інша локація": favorites first (storage already orders them so)."""
    ordered = sorted(locs, key=lambda l: not l.is_favorite)  # stable: keeps creation order
    rows = [
        [Btn(location_label(l, default_id), callback_data=encode("f", mode, l.id))] for l in ordered
    ]
    rows.append([Btn(texts.BTN_MENU, callback_data=encode("m"))])
    return InlineKeyboardMarkup(rows)


def cancel_only() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[Btn(texts.BTN_CANCEL, callback_data=encode("x"))]])


def single_candidate() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        Btn(texts.BTN_ADD, callback_data=encode("Lc", 0)),
        Btn(texts.BTN_ADD_DEFAULT, callback_data=encode("Lcd", 0)),
        Btn(texts.BTN_CANCEL_PLAIN, callback_data=encode("x")),
    ]])


def _short(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def candidates(cands: Sequence[tuple[str, str]]) -> InlineKeyboardMarkup:
    """``cands`` = [(title, description), ...] in user_data order."""
    rows = [
        [Btn(
            texts.CANDIDATE_BUTTON.format(title=t, description=_short(d, _CAND_DESCR_MAX)) if d else t,
            callback_data=encode("Lc", i),
        )]
        for i, (t, d) in enumerate(cands)
    ]
    rows.append([Btn(texts.BTN_CANCEL, callback_data=encode("x"))])
    return InlineKeyboardMarkup(rows)


def subscriptions(
    presets: dict[str, Subscription | None],
    weekly: Subscription | None,
    customs: Sequence[tuple[Subscription, str]],
) -> InlineKeyboardMarkup:
    """Subscriptions screen. ``customs`` = [(sub, location label), ...]."""
    rows: list[list[Btn]] = []
    for preset, (time_local, mode) in PRESETS.items():
        sub = presets.get(preset)
        mark = "✅" if sub is not None and sub.enabled else "⬜"
        rows.append([Btn(
            texts.SUB_PRESET_BUTTON.format(mark=mark, time=time_local, mode=texts.MODE_PLAIN[mode]),
            callback_data=encode("Sp", preset),
        )])
    w_mask = weekly.days_mask if weekly else WEEKLY_DEFAULT_MASK
    w_day = next((texts.WEEKDAYS_SHORT[b] for b in range(7) if w_mask >> b & 1), "?")
    rows.append([Btn(
        texts.SUB_WEEKLY_BUTTON.format(
            mark="✅" if weekly is not None and weekly.enabled else "⬜",
            day=w_day, time=weekly.time_local if weekly else WEEKLY_DEFAULT_TIME,
        ),
        callback_data=encode("Sw"),
    )])
    for sub, loc_label in customs:
        rows.append([Btn(
            texts.SUB_CUSTOM_BUTTON.format(
                mark="🔔" if sub.enabled else "🔕", time=sub.time_local,
                mode=texts.MODE_PLAIN[sub.mode], location=loc_label,
                days=texts.days_label(sub.days_mask),
            ),
            callback_data=encode("Sv", sub.id),
        )])
    rows.append([
        Btn(texts.BTN_CUSTOM_SUB, callback_data=encode("Sc")),
        Btn(texts.BTN_MENU, callback_data=encode("m")),
    ])
    return InlineKeyboardMarkup(rows)


# --- subscriptions: weekly digest / wizard / card ------------------------------

WEEKLY_TIMES: tuple[str, ...] = ("09:00", "12:00", "18:00", "19:00", "20:00")
QUICK_TIMES: tuple[str, ...] = ("07:00", "07:30", "09:00", "18:00", "21:00")
DAY_PRESETS: tuple[tuple[str, int], ...] = (
    (texts.BTN_DAYS_ALL, 127), (texts.BTN_DAYS_WORK, 31), (texts.BTN_DAYS_WEEKEND, 96),
)


def _selected(label: str, on: bool) -> str:
    return f"• {label} •" if on else label


def weekly_screen(sub: Subscription) -> InlineKeyboardMarkup:
    """On/off toggle, Mon..Sun day row, time row, back."""
    toggle = texts.BTN_WEEKLY_ON if sub.enabled else texts.BTN_WEEKLY_OFF
    days = [
        Btn(_selected(texts.WEEKDAYS_SHORT[d], bool(sub.days_mask >> d & 1)),
            callback_data=encode("Swd", d))
        for d in range(7)
    ]
    times = [
        Btn(_selected(t, t == sub.time_local), callback_data=encode("Swt", t))
        for t in WEEKLY_TIMES
    ]
    return InlineKeyboardMarkup([
        [Btn(toggle, callback_data=encode("Swo"))],
        days[:4], days[4:],
        times,
        [Btn(texts.BTN_BACK, callback_data=encode("S"))],
    ])


def wizard_time() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [Btn(t, callback_data=encode("Sct", t)) for t in QUICK_TIMES],
        [Btn(texts.BTN_CANCEL, callback_data=encode("x"))],
    ])


def wizard_mode() -> InlineKeyboardMarkup:
    b = [Btn(texts.MODE_BUTTONS[m], callback_data=encode("Scm", m)) for m in MODES]
    return InlineKeyboardMarkup([b[:2], b[2:], [Btn(texts.BTN_CANCEL, callback_data=encode("x"))]])


def wizard_days() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [Btn(label, callback_data=encode("Scd", mask)) for label, mask in DAY_PRESETS],
        [Btn(texts.BTN_CANCEL, callback_data=encode("x"))],
    ])


def wizard_location(locs: Sequence[Location], default_id: int | None) -> InlineKeyboardMarkup:
    """"Follow default" first, then favorites, then the rest (creation order)."""
    ordered = sorted(locs, key=lambda l: not l.is_favorite)
    rows = [[Btn(texts.BTN_SUB_DEFAULT_LOC, callback_data=encode("Scl", 0))]]
    rows += [[Btn(location_label(l, default_id), callback_data=encode("Scl", l.id))] for l in ordered]
    rows.append([Btn(texts.BTN_CANCEL, callback_data=encode("x"))])
    return InlineKeyboardMarkup(rows)


def sub_card(sub: Subscription) -> InlineKeyboardMarkup:
    toggle = texts.BTN_SUB_DISABLE if sub.enabled else texts.BTN_SUB_ENABLE
    return InlineKeyboardMarkup([
        [Btn(toggle, callback_data=encode("Se", sub.id))],
        [Btn(texts.BTN_DELETE, callback_data=encode("Sx", sub.id))],
        [Btn(texts.BTN_BACK, callback_data=encode("S"))],
    ])


def sub_delete_confirm(sub_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        Btn(texts.BTN_DELETE_YES, callback_data=encode("Sxy", sub_id)),
        Btn(texts.BTN_CANCEL_PLAIN, callback_data=encode("Sv", sub_id)),
    ]])
