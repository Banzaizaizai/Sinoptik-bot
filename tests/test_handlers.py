"""Handler-level tests with fake Update/Context objects and a real tmp SQLite DB."""

import asyncio
from types import SimpleNamespace

import pytest

from weather_bot.bot import handlers as h
from weather_bot.bot import storage
from weather_bot.fetcher import FetchError
from weather_bot.locations import Candidate, GeocoderError

OWNER = 114665985


class FakeMessage:
    def __init__(self, log, text=None):
        self.log = log
        self.text = text

    async def reply_text(self, text, **kw):
        self.log.append(("reply", text, kw))
        return FakeMessage(self.log)

    async def edit_text(self, text, **kw):
        self.log.append(("edit_status", text, kw))


class FakeQuery:
    def __init__(self, data, log):
        self.data = data
        self.message = object()
        self.log = log

    async def answer(self, text=None, **kw):
        self.log.append(("answer", text))

    async def edit_message_text(self, text, **kw):
        self.log.append(("edit", text, kw))


class FakeBot:
    def __init__(self, log):
        self.log = log

    async def send_message(self, chat_id, text, **kw):
        self.log.append(("send", text, kw))

    async def send_chat_action(self, *a, **k):
        pass


class FakeService:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    async def get_forecast(self, slug, title, mode):
        self.calls.append((slug, title, mode))
        if self.error:
            raise self.error
        return f"FORECAST {title} {mode}"


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "bot.sqlite3"
    storage.migrate(db)
    storage.upsert_user(db, OWNER)
    storage.seed_default(db, OWNER)
    log = []
    cfg = SimpleNamespace(db_path=db, allowed_chat_ids=(OWNER,), owner_id=OWNER)
    service = FakeService()
    ctx = SimpleNamespace(
        bot_data={"cfg": cfg, "service": service}, user_data={}, bot=FakeBot(log),
        args=[], job_queue=None,
    )
    return SimpleNamespace(db=db, log=log, ctx=ctx, service=service)


def _upd(env, data=None, chat_type="private", chat_id=OWNER, text=None):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
        callback_query=FakeQuery(data, env.log) if data is not None else None,
        effective_message=FakeMessage(env.log, text),
    )


def click(env, data, **kw):
    asyncio.run(h.on_callback(_upd(env, data, **kw), env.ctx))


def last(env, kind):
    return [e for e in env.log if e[0] == kind][-1]


def buttons(entry):
    markup = entry[2]["reply_markup"]
    return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]


def test_main_menu_shows_default_and_favorites(env):
    click(env, "m")
    assert "Дрогобич" in last(env, "edit")[1]
    btns = buttons(last(env, "edit"))
    assert ("★ Дрогобич", "Lv:1") in btns
    labels = [t for t, _ in btns]
    assert labels.index("★ Дрогобич") < labels.index("📍 Локації")
    assert "🔁 Інша локація" not in labels  # only one location


def test_forecast_default_sends_new_message_with_period_row(env):
    click(env, "f:t:0")
    assert env.service.calls == [("drohobych", "Дрогобич", "today")]
    assert last(env, "send")[1] == "FORECAST Дрогобич today"
    data = [cb for _, cb in buttons(last(env, "send"))]
    assert data == ["f:t:1", "f:m:1", "f:w:1", "f:x:1", "m"]
    assert ("answer", "Завантажую…") in env.log


def test_forecast_error_reports_and_keeps_footer(env):
    env.ctx.bot_data["service"] = FakeService(FetchError("boom"))
    click(env, "f:w:1")
    assert "Не вдалося" in last(env, "send")[1]


def test_forecast_foreign_location_not_found(env):
    storage.upsert_user(env.db, 999)
    other = storage.add_location(env.db, 999, "Стрий", "stryi")
    click(env, f"f:t:{other.id}")
    assert ("answer", "Не знайдено") in env.log
    assert env.service.calls == []


def test_no_default_location_prompts_add(env):
    storage.delete_location(env.db, OWNER, 1)
    click(env, "f:t:0")
    assert ("answer", "Спочатку додайте локацію 📍") in env.log
    assert env.ctx.user_data["await"] == "loc_query"


def test_stale_callback(env):
    click(env, "bogus:1")
    assert ("answer", "Меню застаріло") in env.log
    assert "Дрогобич" in last(env, "send")[1]


def test_stranger_and_group_are_ignored(env):
    click(env, "m", chat_id=42)
    click(env, "m", chat_type="group")
    assert [e for e in env.log if e[0] != "answer"] == []


def test_favorite_toggle_and_card_buttons(env):
    click(env, "Lv:1")
    btns = [t for t, _ in buttons(last(env, "edit"))]
    assert "★ Прибрати з обраного" in btns
    assert "⭐ Зробити основною" not in btns  # already default
    click(env, "Lf:1")
    assert storage.get_location(env.db, 1).is_favorite is False
    assert "☆ В обране" in [t for t, _ in buttons(last(env, "edit"))]


def test_locations_list_marks(env):
    storage.add_location(env.db, OWNER, "Стрий", "stryi")
    click(env, "L")
    labels = [t for t, _ in buttons(last(env, "edit"))]
    assert labels[:2] == ["⭐★ Дрогобич", "Стрий"]


def test_other_location_picker_lists_favorites_first(env):
    s = storage.add_location(env.db, OWNER, "Стрий", "stryi")
    b = storage.add_location(env.db, OWNER, "Борислав", "boryslav")
    storage.toggle_favorite(env.db, OWNER, b.id)
    storage.toggle_favorite(env.db, OWNER, 1)  # Дрогобич no longer favorite
    click(env, "m")
    assert ("🔁 Інша локація", "fl:t") in buttons(last(env, "edit"))
    click(env, "fl:t")
    data = [cb for _, cb in buttons(last(env, "edit"))]
    assert data == [f"f:t:{b.id}", "f:t:1", f"f:t:{s.id}", "m"]


def test_make_default_and_delete_promotes(env):
    s = storage.add_location(env.db, OWNER, "Стрий", "stryi")
    click(env, f"Ld:{s.id}")
    assert storage.get_default_location(env.db, OWNER).id == s.id
    click(env, f"Lr:{s.id}")
    assert "Видалити «Стрий»?" in last(env, "edit")[1]
    click(env, f"Lry:{s.id}")
    assert storage.get_location(env.db, s.id) is None
    assert storage.get_default_location(env.db, OWNER).id == 1


def _search(env, text, result):
    def resolver(q):
        if isinstance(result, Exception):
            raise result
        return result

    env.ctx.bot_data["resolve"] = resolver
    asyncio.run(h.on_text(_upd(env, text=text), env.ctx))


STRYI = Candidate("stryi", "Стрий", "Львівська область", 49.26, 23.85, 59000)
STRYI2 = Candidate("stryi-x", "Стрий", "Інша область", 48.0, 24.0, 10)


def test_add_location_single_hit_then_add_as_default(env):
    click(env, "La")
    assert env.ctx.user_data["await"] == "loc_query"
    _search(env, "Стрий", [STRYI])
    assert "Знайдено: <b>Стрий</b>" in last(env, "edit_status")[1]
    click(env, "Lcd:0")
    loc = storage.find_location_by_title(env.db, OWNER, "стрий")
    assert loc is not None and storage.get_default_location(env.db, OWNER).id == loc.id
    assert "✅ Додано «Стрий»." in last(env, "edit")[1]
    assert env.ctx.user_data == {}


def test_add_location_many_hits_and_duplicate(env):
    click(env, "La")
    _search(env, "Стрий", [STRYI, STRYI2])
    data = [cb for _, cb in buttons(last(env, "edit_status"))]
    assert data == ["Lc:0", "Lc:1", "x"]
    click(env, "Lc:0")
    env.ctx.user_data["cands"] = [
        {"slug": "stryi", "title": "Стрий", "description": "", "lat": 1, "lng": 2}
    ]
    click(env, "Lc:0")
    assert ("answer", "«Стрий» уже є у списку.") in env.log


def test_add_location_validation_not_found_and_unavailable(env):
    click(env, "La")
    _search(env, "Ст@рий", [STRYI])
    assert "2–60" in last(env, "reply")[1]
    _search(env, "Нікуди", [])
    assert "Не знайшов" in last(env, "edit_status")[1]
    assert env.ctx.user_data["await"] == "loc_query"  # keeps waiting
    _search(env, "Стрий", GeocoderError("down"))
    assert "тимчасово недоступний" in last(env, "edit_status")[1]


def test_not_found_escaped_name_is_rendered_as_html(env):
    click(env, "La")
    _search(env, "Кам'янець", [])
    _, text, kw = last(env, "edit_status")
    assert "&#x27;" in text and kw.get("parse_mode") == "HTML"


def test_stale_candidates(env):
    click(env, "Lc:3")
    assert ("answer", "Пошук застарів, повторіть") in env.log
    assert env.ctx.user_data["await"] == "loc_query"


def test_cancel_clears_state(env):
    env.ctx.user_data.update({"await": "loc_query", "cands": [1]})
    click(env, "x")
    assert env.ctx.user_data == {}


@pytest.mark.parametrize("q,ok", [
    ("Стрий", True), ("Камʼянець-Подільський", True), ("Нова Ушиця", True),
    ("stryi", True), ("ab_1", False), ("drohobych-2", True), ("С", False), ("<b>", False), ("А" * 61, False),
])
def test_valid_location_query(q, ok):
    assert h.valid_location_query(q) is ok


def test_forecast_command_with_saved_and_unsaved_location(env):
    storage.add_location(env.db, OWNER, "Стрий", "stryi")
    env.ctx.args = ["стрий"]
    asyncio.run(h.cmd_week(_upd(env), env.ctx))
    assert env.service.calls[-1] == ("stryi", "Стрий", "week")
    env.ctx.args = ["Трускавець"]
    env.ctx.bot_data["resolve"] = lambda q: [Candidate("truskavets", "Трускавець", "", 0, 0)]
    asyncio.run(h.cmd_10days(_upd(env), env.ctx))
    assert env.service.calls[-1] == ("truskavets", "Трускавець", "10days")
    assert [cb for _, cb in buttons(last(env, "send"))] == ["m"]  # unsaved: no period row


def test_subscriptions_screen_renders(env):
    storage.toggle_preset(env.db, OWNER, "morning")
    click(env, "S")
    labels = [t for t, _ in buttons(last(env, "edit"))]
    assert labels[0] == "✅ 08:00 · сьогодні"
    assert labels[1] == "⬜ 13:00 · сьогодні"
    assert "⬜ Тижневий огляд · Нд 19:00" in labels
