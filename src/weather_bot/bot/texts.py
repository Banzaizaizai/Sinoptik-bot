"""All user-facing Ukrainian strings of the interactive bot.

Templates use ``str.format``; any user/remote value must be ``html.escape``d by
the caller before substitution (messages are sent with ``parse_mode=HTML``).
"""

from __future__ import annotations

from typing import Final

# --- forecast mode labels ---------------------------------------------------
MODE_BUTTONS: Final[dict[str, str]] = {
    "today": "☀️ Сьогодні",
    "tomorrow": "🌙 Завтра",
    "week": "📅 Тиждень",
    "10days": "🗓 10 днів",
}
MODE_PLAIN: Final[dict[str, str]] = {
    "today": "сьогодні",
    "tomorrow": "на завтра",
    "week": "тиждень",
    "10days": "10 днів",
}

# --- generic buttons --------------------------------------------------------
BTN_MENU: Final = "🏠 Меню"
BTN_BACK: Final = "⬅️ Назад"
BTN_CANCEL: Final = "✖ Скасувати"
BTN_CANCEL_PLAIN: Final = "Скасувати"
BTN_OTHER_LOCATION: Final = "🔁 Інша локація"
BTN_LOCATIONS: Final = "📍 Локації"
BTN_SUBS: Final = "🔔 Розсилки"
BTN_FAVORITE_QUICK: Final = "★ {title}"

# --- main menu --------------------------------------------------------------
MENU_HEADER: Final = "🌤 Погода · 📍 <b>{title}</b>"
MENU_NO_LOCATION: Final = "🌤 Погода · 📍 Локацію не вибрано"
NEED_LOCATION_FIRST: Final = "Спочатку додайте локацію 📍"
PICK_LOCATION_FOR: Final = "Оберіть локацію ({mode}):"

# --- forecast ---------------------------------------------------------------
LOADING: Final = "Завантажую…"
FORECAST_FAILED: Final = (
    "😕 Не вдалося отримати прогноз для {title} (sinoptik.ua недоступний). "
    "Спробуйте пізніше."
)
LOCATION_ARG_NOT_FOUND: Final = "Не знайшов «{q}». Додайте локацію через 📍 Локації."

# --- locations --------------------------------------------------------------
LOCATIONS_HEADER: Final = "📍 <b>Ваші локації</b>\n⭐ — за замовчуванням · ★ — обране"
LOCATIONS_EMPTY: Final = "📍 <b>Ваші локації</b>\nПоки що порожньо."
BTN_ADD_LOCATION: Final = "➕ Додати локацію"
MARK_DEFAULT: Final = "⭐"
MARK_FAVORITE: Final = "★"

LOCATION_CARD: Final = "<b>{title}</b>\n{description}"
LOCATION_CARD_DEFAULT_NOTE: Final = "⭐ Локація за замовчуванням"
BTN_MAKE_DEFAULT: Final = "⭐ Зробити основною"
BTN_FAV_ADD: Final = "☆ В обране"
BTN_FAV_REMOVE: Final = "★ Прибрати з обраного"
BTN_DELETE: Final = "🗑 Видалити"
DEFAULT_SET: Final = "⭐ «{title}» тепер основна"
FAV_ADDED: Final = "★ Додано в обране"
FAV_REMOVED: Final = "Прибрано з обраного"

DELETE_CONFIRM: Final = "Видалити «{title}»? Розсилки цієї локації теж буде видалено ({n})."
BTN_DELETE_YES: Final = "Так, видалити"
DELETED: Final = "🗑 Видалено «{title}»"

NOT_FOUND: Final = "Не знайдено"
STALE_MENU: Final = "Меню застаріло"

# --- add location -----------------------------------------------------------
ASK_LOCATION: Final = "Напишіть назву населеного пункту (напр.: Стрий, Трускавець)."
BAD_LOCATION_INPUT: Final = (
    "Назва має містити 2–60 літер (дозволені пробіл, дефіс і апостроф). "
    "Або введіть slug зі sinoptik.ua, напр. stryi."
)
SEARCHING: Final = "🔎 Шукаю…"
SEARCH_NOT_FOUND: Final = (
    "Не знайшов «{q}» на sinoptik.ua. Підтримуються міста та більші населені пункти. "
    "Спробуйте іншу назву або slug зі sinoptik.ua."
)
SEARCH_UNAVAILABLE: Final = "Пошук тимчасово недоступний. Можна ввести slug зі sinoptik.ua."
FOUND_ONE: Final = "Знайдено: <b>{title}</b>\n{description}"
FOUND_MANY: Final = "Знайдено кілька варіантів:"
FOUND_MANY_LINE: Final = "{n}. <b>{title}</b> — {description}"
BTN_ADD: Final = "✅ Додати"
BTN_ADD_DEFAULT: Final = "⭐ Додати як основну"
CANDIDATE_BUTTON: Final = "{title} · {description}"
ADDED: Final = "✅ Додано «{title}»."
ALREADY_EXISTS: Final = "«{title}» уже є у списку."
LOCATIONS_LIMIT: Final = "Досягнуто ліміту локацій ({n})."
SEARCH_STALE: Final = "Пошук застарів, повторіть"

# --- subscriptions (screen rendered here; actions arrive with the scheduler) -
SUBS_HEADER: Final = (
    "🔔 <b>Розсилки</b> (час — Київ; пресети — для локації за замовчуванням)"
)
SUB_PRESET_BUTTON: Final = "{mark} {time} · {mode}"
SUB_WEEKLY_BUTTON: Final = "{mark} Тижневий огляд · {day} {time}"
SUB_CUSTOM_BUTTON: Final = "{mark} {time} · {mode} · {location} · {days}"
BTN_CUSTOM_SUB: Final = "➕ Своя розсилка"
DEFAULT_LOCATION_LABEL: Final = "основна"

SUB_TOGGLED_ON: Final = "Увімкнено: {time} {mode}"
SUB_TOGGLED_OFF: Final = "Вимкнено: {time} {mode}"

# weekly digest screen
WEEKLY_SCREEN: Final = (
    "🗞 <b>Тижневий огляд</b>\nПрогноз на тиждень для локації за замовчуванням.\n"
    "Стан: {state} · {day} {time} (Київ)"
)
WEEKLY_ON: Final = "увімкнено"
WEEKLY_OFF: Final = "вимкнено"
BTN_WEEKLY_ON: Final = "✅ Увімкнено"
BTN_WEEKLY_OFF: Final = "⬜ Вимкнено"
WEEKLY_DIGEST_HEADER: Final = "🗞 Тижневий огляд"

# custom subscription wizard
ASK_TIME: Final = "Вкажіть час ГГ:ХХ (Київ), напр. 07:30"
BAD_TIME: Final = "Не розпізнав час. Приклад: 07:30"
DST_HOUR: Final = (
    "⚠️ 03:00–03:59 — година переходу на літній/зимовий час, оберіть інший час."
)
ASK_MODE: Final = "⏰ {time}\nЩо надсилати?"
ASK_DAYS: Final = "⏰ {time} · {mode}\nКоли?"
ASK_SUB_LOCATION: Final = "⏰ {time} · {mode} · {days}\nДля якої локації?"
BTN_DAYS_ALL: Final = "Щодня"
BTN_DAYS_WORK: Final = "Пн–Пт"
BTN_DAYS_WEEKEND: Final = "Сб–Нд"
BTN_SUB_DEFAULT_LOC: Final = "⭐ Основна (завжди поточна)"
SUB_DUPLICATE: Final = "Така розсилка вже є."
SUB_LIMIT: Final = "Досягнуто ліміту ({n})."
SUB_CREATED: Final = (
    "✅ Розсилку створено: {time} · {mode} · {location} · {days}. "
    "Наступне надсилання: {next}."
)
SUB_DRAFT_STALE: Final = "Майстер застарів, почніть знову"
NEXT_RUN: Final = "{wd} {d:%d.%m} о {d:%H:%M}"
WEEKDAYS_LOWER: Final = ("пн", "вт", "ср", "чт", "пт", "сб", "нд")

# subscription card
SUB_CARD: Final = (
    "{mark} <b>{time}</b> · {mode}\n📍 {location}\n📆 {days}\nСтан: {state}"
)
SUB_STATE_ON: Final = "увімкнено"
SUB_STATE_OFF: Final = "вимкнено"
BTN_SUB_DISABLE: Final = "🔕 Вимкнути"
BTN_SUB_ENABLE: Final = "🔔 Увімкнути"
SUB_DELETE_CONFIRM: Final = "Видалити розсилку {time} · {mode} · {location}?"
SUB_DELETED: Final = "🗑 Розсилку видалено"

# scheduled sends
SUB_NO_LOCATION: Final = "⚠️ Розсилку {time} пропущено: немає локації за замовчуванням."
SUB_FETCH_FAILED: Final = (
    "⚠️ Не вдалося отримати прогноз для {title} (sinoptik.ua недоступний). "
    "Спробуйте /today пізніше."
)

WEEKDAYS_SHORT: Final = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд")
DAYS_EVERYDAY: Final = "щодня"
DAYS_WORKDAYS: Final = "Пн–Пт"
DAYS_WEEKEND: Final = "Сб–Нд"

# --- misc -------------------------------------------------------------------
CANCELLED: Final = "Скасовано."
ERROR_GENERIC: Final = "😕 Сталася помилка. Спробуйте ще раз."
ALERT_ERROR: Final = "⚠️ Помилка в боті: {error}"
ALERT_PARSE: Final = "⚠️ sinoptik змінив розмітку? {n} помилки розбору поспіль. Остання: {error}"

# --- bot commands (set_my_commands) ----------------------------------------
COMMANDS: Final[tuple[tuple[str, str], ...]] = (
    ("start", "Головне меню"),
    ("menu", "Головне меню"),
    ("today", "Прогноз на сьогодні [локація]"),
    ("tomorrow", "Прогноз на завтра [локація]"),
    ("week", "Прогноз на тиждень [локація]"),
    ("10days", "Прогноз на 10 днів [локація]"),
    ("locations", "Мої локації"),
    ("subs", "Розсилки"),
    ("cancel", "Скасувати дію"),
)


def days_label(mask: int) -> str:
    """Human label of a Mon-based days mask (bit0=Mon..bit6=Sun)."""
    if mask == 127:
        return DAYS_EVERYDAY
    if mask == 31:
        return DAYS_WORKDAYS
    if mask == 96:
        return DAYS_WEEKEND
    return ",".join(WEEKDAYS_SHORT[b] for b in range(7) if mask >> b & 1)
