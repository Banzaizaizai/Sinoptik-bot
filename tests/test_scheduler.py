"""Scheduler: weekday mapping, real PTB JobQueue wiring, DST, catch-up, fire() dedup,
plus the subscription UI (presets, weekly, wizard, card). No network."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from telegram.error import Forbidden, NetworkError, RetryAfter, TimedOut
from telegram.ext import Application

from test_handlers import OWNER, FakeBot, FakeService, _upd, buttons, last
from weather_bot.bot import handlers as h
from weather_bot.bot import scheduler as sch
from weather_bot.bot import storage
from weather_bot.fetcher import FetchError

KYIV = ZoneInfo("Europe/Kyiv")
STRANGER = 777


def kyiv(*a: int) -> datetime:
    return datetime(*a, tzinfo=KYIV)


@pytest.fixture
def jq():
    app = Application.builder().token("1:x").updater(None).build()
    return app.job_queue


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "bot.sqlite3"
    storage.migrate(p)
    storage.upsert_user(p, OWNER)
    storage.seed_default(p, OWNER)
    return p


def sub_jobs(jq):
    return [j for j in jq.jobs() if j.name and j.name.startswith("sub:")]


# --- ptb_days / real JobQueue --------------------------------------------------


def test_ptb_days_mapping() -> None:
    assert sch.ptb_days(64) == (0,)
    assert sch.ptb_days(1) == (1,)
    assert sch.ptb_days(31) == (1, 2, 3, 4, 5)
    assert sch.ptb_days(96) == (0, 6)
    assert sch.ptb_days(127) == (0, 1, 2, 3, 4, 5, 6)


def test_real_jobqueue_sunday_and_job_kwargs(jq) -> None:
    j = jq.run_daily(
        sch.fire, time=time(19, 0, tzinfo=KYIV), days=sch.ptb_days(64), name="sub:1",
        data={"id": 1}, job_kwargs=dict(sch.JOB_KWARGS),
    )
    nxt = j.job.trigger.get_next_fire_time(None, kyiv(2026, 11, 9, 12, 0))  # a Monday
    assert nxt.weekday() == 6  # Sunday
    assert (nxt.date(), nxt.hour, nxt.minute) == (date(2026, 11, 15), 19, 0)
    assert j.job.misfire_grace_time == 900
    assert j.job.coalesce is True
    assert j.job.max_instances == 1


def test_real_jobqueue_workdays(jq, db) -> None:
    sub = storage.create_custom(db, OWNER, time_local="07:30", mode="today", days_mask=31)
    sch.schedule_sub(jq, sub, KYIV)
    (j,) = jq.get_jobs_by_name("sub:%d" % sub.id)
    start = kyiv(2026, 11, 13, 8, 0)  # Friday after 07:30 -> next is Monday
    nxt = j.job.trigger.get_next_fire_time(None, start)
    assert nxt == kyiv(2026, 11, 16, 7, 30)
    # next_run() (used for the confirmation message) agrees with the PTB job.
    assert sch.next_run(sub, KYIV, start) == nxt


def test_schedule_unschedule_and_disabled(jq, db) -> None:
    sub = storage.create_custom(db, OWNER, time_local="07:30", mode="today")
    sch.schedule_sub(jq, sub, KYIV)
    sch.schedule_sub(jq, sub, KYIV)  # replaces, never duplicates
    assert len(jq.get_jobs_by_name(f"sub:{sub.id}")) == 1
    off = storage.set_subscription_enabled(db, OWNER, sub.id, False)
    sch.schedule_sub(jq, off, KYIV)
    assert jq.get_jobs_by_name(f"sub:{sub.id}") == ()
    sch.unschedule_sub(jq, sub.id)  # idempotent
    sch.unschedule_sub(None, sub.id)  # no JobQueue: no-op


def test_rebuild_all_only_allowed_and_enabled(jq, db) -> None:
    a = storage.create_custom(db, OWNER, time_local="07:30", mode="today")
    b = storage.create_custom(db, OWNER, time_local="08:30", mode="today")
    storage.set_subscription_enabled(db, OWNER, b.id, False)
    storage.upsert_user(db, STRANGER)
    storage.create_custom(db, STRANGER, time_local="09:30", mode="today")
    jq.run_daily(sch.fire, time=time(1, 0, tzinfo=KYIV), name="sub:999")  # stale job
    assert sch.rebuild_all(jq, db, (OWNER,), KYIV) == 1
    assert [j.name for j in sub_jobs(jq)] == [f"sub:{a.id}"]


# --- DST ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "start", [kyiv(2027, 3, 26, 12, 0), kyiv(2027, 10, 29, 12, 0)], ids=["spring", "autumn"]
)
def test_cron_keeps_wall_clock_across_dst(jq, start) -> None:
    j = jq.run_daily(
        sch.fire, time=time(8, 0, tzinfo=KYIV), days=sch.ptb_days(127), name="sub:5",
        job_kwargs=dict(sch.JOB_KWARGS),
    )
    t, offsets = start, set()
    for _ in range(4):
        t = j.job.trigger.get_next_fire_time(None, t + timedelta(seconds=1))
        local = t.astimezone(KYIV)
        assert (local.hour, local.minute) == (8, 0)
        offsets.add(local.utcoffset())
    assert offsets == {timedelta(hours=2), timedelta(hours=3)}  # crossed the change


# --- fire_date / catch-up ------------------------------------------------------


def test_compute_fire_date() -> None:
    assert sch.compute_fire_date("08:00", kyiv(2026, 11, 9, 8, 0, 1)) == date(2026, 11, 9)
    assert sch.compute_fire_date("08:00", kyiv(2026, 11, 9, 8, 14)) == date(2026, 11, 9)
    # 23:55 job running late at 00:05 belongs to the previous day.
    assert sch.compute_fire_date("23:55", kyiv(2026, 11, 10, 0, 5)) == date(2026, 11, 9)


def test_catch_up_window(jq, db) -> None:
    now = kyiv(2026, 11, 9, 8, 5)  # Monday
    recent = storage.create_custom(db, OWNER, time_local="08:00", mode="today")
    old = storage.create_custom(db, OWNER, time_local="07:35", mode="today")
    logged = storage.create_custom(db, OWNER, time_local="08:01", mode="today")
    storage.try_start_send(db, logged.id, "2026-11-09")
    storage.mark_send(db, logged.id, "2026-11-09", "sent")
    storage.create_custom(db, OWNER, time_local="08:02", mode="today", days_mask=96)  # weekend
    storage.create_custom(db, OWNER, time_local="08:10", mode="today")  # still in the future
    ids = sch.catch_up(jq, db, (OWNER,), KYIV, now)
    assert ids == [recent.id]
    (j,) = jq.get_jobs_by_name(f"catchup:{recent.id}")
    assert j.data == {"id": recent.id, "fire_date": "2026-11-09"}
    assert old.id not in ids
    # Queued before the scheduler starts: needs a real grace time, not 1 s.
    assert j.job.misfire_grace_time == sch.MISFIRE_GRACE_SEC


def test_catch_up_resumes_stuck_sending_row(jq, db) -> None:
    sub = storage.create_custom(db, OWNER, time_local="08:00", mode="today")
    storage.try_start_send(db, sub.id, "2026-11-09")  # crashed mid-send
    ids = sch.catch_up(jq, db, (OWNER,), KYIV, kyiv(2026, 11, 9, 8, 5))
    assert ids == [sub.id]
    (j,) = jq.get_jobs_by_name(f"catchup:{sub.id}")
    assert j.data == {"id": sub.id, "fire_date": "2026-11-09", "retry": True}


def test_catch_up_across_midnight(db) -> None:
    sub = storage.create_custom(db, OWNER, time_local="23:55", mode="today")
    due = sch.due_catch_ups(
        storage.list_enabled_subscriptions(db), db, (OWNER,), kyiv(2026, 11, 10, 0, 5)
    )
    assert [(s.id, d) for s, d in due] == [(sub.id, date(2026, 11, 9))]


def test_catch_up_skips_strangers(db) -> None:
    storage.upsert_user(db, STRANGER)
    storage.create_custom(db, STRANGER, time_local="08:00", mode="today")
    assert sch.due_catch_ups(
        storage.list_enabled_subscriptions(db), db, (OWNER,), kyiv(2026, 11, 9, 8, 5)
    ) == []


# --- fire() --------------------------------------------------------------------


class FakeJQ:
    def __init__(self) -> None:
        self.once: list[tuple] = []
        self.removed: list[str] = []

    def run_once(self, cb, when, data=None, name=None, chat_id=None, job_kwargs=None):
        self.once.append((when, data, name))
        self.kwargs = job_kwargs

    def get_jobs_by_name(self, name):
        self.removed.append(name)
        return ()


def make_ctx(db, data, *, service=None, bot=None, log=None):
    log = log if log is not None else []
    cfg = SimpleNamespace(
        db_path=db, allowed_chat_ids=(OWNER,), owner_id=OWNER, timezone="Europe/Kyiv"
    )
    return SimpleNamespace(
        job=SimpleNamespace(data=data), bot_data={"cfg": cfg, "service": service or FakeService()},
        bot=bot or FakeBot(log), job_queue=FakeJQ(), log=log,
    )


@pytest.fixture
def frozen(monkeypatch):
    holder = {"now": kyiv(2026, 11, 9, 8, 0, 5)}
    monkeypatch.setattr(sch, "now_kyiv", lambda tz: holder["now"])
    return holder


def run(coro):
    return asyncio.run(coro)


def test_fire_twice_sends_once(db, frozen) -> None:
    sub = storage.create_custom(db, OWNER, time_local="08:00", mode="today")
    ctx = make_ctx(db, {"id": sub.id})
    run(sch.fire(ctx))
    run(sch.fire(ctx))
    sends = [e for e in ctx.log if e[0] == "send"]
    assert len(sends) == 1
    assert sends[0][1] == "FORECAST Дрогобич today"
    assert storage.get_send_log(db, sub.id, "2026-11-09").status == "sent"
    # Footer is the period-switch keyboard for the location.
    assert ("☀️ Сьогодні", "f:t:1") in buttons(sends[0])


def test_fire_concurrent_dedup(db, frozen) -> None:
    sub = storage.create_custom(db, OWNER, time_local="08:00", mode="today")
    ctx = make_ctx(db, {"id": sub.id})

    async def both():
        await asyncio.gather(sch.fire(ctx), sch.fire(ctx))

    run(both())
    assert len([e for e in ctx.log if e[0] == "send"]) == 1


def test_late_fire_after_midnight_keeps_previous_date(db, frozen) -> None:
    sub = storage.create_custom(db, OWNER, time_local="23:55", mode="tomorrow")
    frozen["now"] = kyiv(2026, 11, 10, 0, 4)
    run(sch.fire(make_ctx(db, {"id": sub.id})))
    assert storage.get_send_log(db, sub.id, "2026-11-09").status == "sent"
    assert storage.get_send_log(db, sub.id, "2026-11-10") is None


def test_fire_catch_up_date_from_job_data(db, frozen) -> None:
    sub = storage.create_custom(db, OWNER, time_local="08:00", mode="today")
    run(sch.fire(make_ctx(db, {"id": sub.id, "fire_date": "2026-11-08"})))
    assert storage.get_send_log(db, sub.id, "2026-11-08").status == "sent"


def test_fire_skips_disabled_missing_and_strangers(db, frozen) -> None:
    sub = storage.create_custom(db, OWNER, time_local="08:00", mode="today")
    storage.set_subscription_enabled(db, OWNER, sub.id, False)
    storage.upsert_user(db, STRANGER)
    foreign = storage.create_custom(db, STRANGER, time_local="08:00", mode="today")
    for sid in (sub.id, foreign.id, 12345):
        ctx = make_ctx(db, {"id": sid})
        run(sch.fire(ctx))
        assert ctx.log == []
        assert storage.get_send_log(db, sid, "2026-11-09") is None


def test_weekly_digest_header(db, frozen) -> None:
    storage.set_weekly_day(db, OWNER, 0)
    weekly = storage.toggle_weekly(db, OWNER)
    ctx = make_ctx(db, {"id": weekly.id})
    run(sch.fire(ctx))
    text = last(ctx, "send")[1]
    assert text.startswith("🗞 Тижневий огляд\n") and "week" in text


def test_fire_no_default_location(db, frozen) -> None:
    sub = storage.toggle_preset(db, OWNER, "morning")
    storage.delete_location(db, OWNER, storage.get_default_location(db, OWNER).id)
    ctx = make_ctx(db, {"id": sub.id})
    run(sch.fire(ctx))
    assert "пропущено" in last(ctx, "send")[1]
    assert storage.get_send_log(db, sub.id, "2026-11-09").status == "noloc"


def test_fetch_error_retries_once_then_fails(db, frozen) -> None:
    sub = storage.create_custom(db, OWNER, time_local="08:00", mode="today")
    svc = FakeService(error=FetchError("down"))
    ctx = make_ctx(db, {"id": sub.id}, service=svc)
    run(sch.fire(ctx))
    assert ctx.log == []
    ((delay, data, name),) = ctx.job_queue.once
    assert delay == 300 and data == {"id": sub.id, "fire_date": "2026-11-09", "retry": True}
    assert storage.get_send_log(db, sub.id, "2026-11-09").status == "sending"

    retry_ctx = make_ctx(db, data, service=svc, log=ctx.log)
    run(sch.fire(retry_ctx))
    assert retry_ctx.job_queue.once == []  # no second retry
    assert "Не вдалося отримати прогноз для Дрогобич" in last(ctx, "send")[1]
    entry = storage.get_send_log(db, sub.id, "2026-11-09")
    assert entry.status == "failed" and "down" in entry.error
    run(sch.fire(retry_ctx))  # a stray duplicate retry does nothing
    assert len([e for e in ctx.log if e[0] == "send"]) == 1


def test_retry_succeeds(db, frozen) -> None:
    sub = storage.create_custom(db, OWNER, time_local="08:00", mode="today")
    ctx = make_ctx(db, {"id": sub.id}, service=FakeService(error=FetchError("down")))
    run(sch.fire(ctx))
    data = ctx.job_queue.once[0][1]
    ctx2 = make_ctx(db, data)
    run(sch.fire(ctx2))
    assert storage.get_send_log(db, sub.id, "2026-11-09").status == "sent"


class FlakyBot(FakeBot):
    def __init__(self, log, exc):
        super().__init__(log)
        self.exc = exc

    async def send_message(self, chat_id, text, **kw):
        if self.exc is not None:
            exc, self.exc = self.exc, None
            raise exc
        await super().send_message(chat_id, text, **kw)


def test_retry_after_is_retried(db, frozen) -> None:
    sub = storage.create_custom(db, OWNER, time_local="08:00", mode="today")
    log: list = []
    ctx = make_ctx(db, {"id": sub.id}, bot=FlakyBot(log, RetryAfter(0)), log=log)
    run(sch.fire(ctx))
    assert len([e for e in log if e[0] == "send"]) == 1
    assert storage.get_send_log(db, sub.id, "2026-11-09").status == "sent"


def test_telegram_error_queues_retry_then_fails(db, frozen) -> None:
    sub = storage.create_custom(db, OWNER, time_local="08:00", mode="today")
    log: list = []
    ctx = make_ctx(db, {"id": sub.id}, bot=FlakyBot(log, TimedOut()), log=log)
    run(sch.fire(ctx))
    assert storage.get_send_log(db, sub.id, "2026-11-09").status == "sending"
    (when, data, name) = ctx.job_queue.once[0]
    assert data["retry"] is True and when == sch.RETRY_DELAY_SEC
    assert ctx.job_queue.kwargs["misfire_grace_time"] == sch.MISFIRE_GRACE_SEC
    # Retry fails too -> recorded as failed, not left at 'sending'.
    ctx2 = make_ctx(db, data, bot=FlakyBot([], NetworkError("boom")))
    run(sch.fire(ctx2))
    assert storage.get_send_log(db, sub.id, "2026-11-09").status == "failed"


def test_unexpected_error_marks_failed(db, frozen) -> None:
    sub = storage.create_custom(db, OWNER, time_local="08:00", mode="today")
    ctx = make_ctx(db, {"id": sub.id}, bot=FlakyBot([], RuntimeError("bug")))
    with pytest.raises(RuntimeError):
        run(sch.fire(ctx))
    assert storage.get_send_log(db, sub.id, "2026-11-09").status == "failed"


def test_forbidden_disables_all_subscriptions(db, frozen) -> None:
    a = storage.create_custom(db, OWNER, time_local="08:00", mode="today")
    b = storage.toggle_preset(db, OWNER, "evening")
    log: list = []
    ctx = make_ctx(db, {"id": a.id}, bot=FlakyBot(log, Forbidden("bot was blocked")), log=log)
    run(sch.fire(ctx))
    assert all(not s.enabled for s in storage.list_subscriptions(db, OWNER))
    assert set(ctx.job_queue.removed) == {f"sub:{a.id}", f"sub:{b.id}"}
    assert storage.get_send_log(db, a.id, "2026-11-09").status == "failed"


# --- subscription UI -------------------------------------------------------------


@pytest.fixture
def ui(db, jq):
    log: list = []
    cfg = SimpleNamespace(
        db_path=db, allowed_chat_ids=(OWNER,), owner_id=OWNER, timezone="Europe/Kyiv"
    )
    ctx = SimpleNamespace(
        bot_data={"cfg": cfg, "service": FakeService()}, user_data={}, bot=FakeBot(log),
        args=[], job_queue=jq,
    )
    return SimpleNamespace(db=db, log=log, ctx=ctx, jq=jq)


def click(env, data):
    asyncio.run(h.on_callback(_upd(env, data), env.ctx))


def type_text(env, text):
    asyncio.run(h.on_text(_upd(env, text=text), env.ctx))


def test_preset_toggle_schedules(ui) -> None:
    click(ui, "Sp:mo")
    sub = storage.get_preset(ui.db, OWNER, "morning")
    assert sub.enabled and ("answer", "Увімкнено: 08:00 сьогодні") in ui.log
    assert len(ui.jq.get_jobs_by_name(f"sub:{sub.id}")) == 1
    assert ("✅ 08:00 · сьогодні", "Sp:mo") in buttons(last(ui, "edit"))
    click(ui, "Sp:mo")
    assert ("answer", "Вимкнено: 08:00 сьогодні") in ui.log
    assert ui.jq.get_jobs_by_name(f"sub:{sub.id}") == ()


def test_weekly_screen(ui) -> None:
    click(ui, "Sw")
    assert "Тижневий огляд" in last(ui, "edit")[1]
    assert ("• Нд •", "Swd:6") in buttons(last(ui, "edit"))
    click(ui, "Swo")
    weekly = storage.get_weekly(ui.db, OWNER)
    assert weekly.enabled
    (j,) = ui.jq.get_jobs_by_name(f"sub:{weekly.id}")
    click(ui, "Swd:4")
    click(ui, "Swt:0900")
    weekly = storage.get_weekly(ui.db, OWNER)
    assert (weekly.days_mask, weekly.time_local) == (16, "09:00")
    (j,) = ui.jq.get_jobs_by_name(f"sub:{weekly.id}")
    nxt = j.job.trigger.get_next_fire_time(None, kyiv(2026, 11, 9, 12, 0))
    assert nxt == kyiv(2026, 11, 13, 9, 0)  # Friday
    assert ("• 09:00 •", "Swt:0900") in buttons(last(ui, "edit"))


def test_wizard_typed_time_single_location(ui, frozen) -> None:
    click(ui, "Sc")
    assert ui.ctx.user_data["await"] == "time"
    assert ("07:30", "Sct:0730") in buttons(last(ui, "edit"))
    type_text(ui, "abc")
    assert last(ui, "reply")[1] == "Не розпізнав час. Приклад: 07:30"
    type_text(ui, "3:15")
    assert "03:00–03:59" in last(ui, "reply")[1]
    type_text(ui, "7.30")
    assert "Що надсилати?" in last(ui, "reply")[1]
    click(ui, "Scm:t")
    click(ui, "Scd:31")  # one location -> no location step
    (sub,) = [s for s in storage.list_subscriptions(ui.db, OWNER) if s.kind == "custom"]
    assert (sub.time_local, sub.mode, sub.days_mask, sub.location_id) == ("07:30", "today", 31, None)
    text = last(ui, "edit")[1]
    assert "✅ Розсилку створено: 07:30 · сьогодні · основна · Пн–Пт." in text
    assert "Наступне надсилання: вт 10.11 о 07:30." in text  # frozen: Mon 09.11 08:00
    assert len(ui.jq.get_jobs_by_name(f"sub:{sub.id}")) == 1
    assert "draft" not in ui.ctx.user_data and "await" not in ui.ctx.user_data


def test_wizard_location_step_favorites_first_and_duplicate(ui, frozen) -> None:
    stryi = storage.add_location(ui.db, OWNER, "Стрий", "stryi")
    sambir = storage.add_location(ui.db, OWNER, "Самбір", "sambir")
    storage.toggle_favorite(ui.db, OWNER, sambir.id)
    for _ in range(2):
        click(ui, "Sc")
        click(ui, "Sct:1800")
        click(ui, "Scm:w")
        click(ui, "Scd:127")
        btns = buttons(last(ui, "edit"))
        assert btns[0] == ("⭐ Основна (завжди поточна)", "Scl:0")
        labels = [t for t, _ in btns[1:4]]
        assert labels == ["⭐★ Дрогобич", "★ Самбір", "Стрий"]
        click(ui, f"Scl:{stryi.id}")
    customs = [s for s in storage.list_subscriptions(ui.db, OWNER) if s.kind == "custom"]
    assert len(customs) == 1 and customs[0].location_id == stryi.id
    assert ("answer", "Така розсилка вже є.") in ui.log


def test_wizard_stale_and_dst_button(ui) -> None:
    click(ui, "Scm:t")  # no draft
    assert ("answer", "Майстер застарів, почніть знову") in ui.log
    click(ui, "Sc")
    click(ui, "Sct:0330")
    assert "03:00–03:59" in last(ui, "answer")[1]
    assert "time" not in ui.ctx.user_data["draft"]


def test_wizard_limit(ui) -> None:
    for i in range(storage.MAX_SUBSCRIPTIONS):
        storage.create_custom(ui.db, OWNER, time_local=f"{i:02d}:10", mode="today")
    click(ui, "Sc")
    assert ("answer", "Досягнуто ліміту (20).") in ui.log
    assert "await" not in ui.ctx.user_data


def test_sub_card_toggle_and_delete(ui) -> None:
    sub = storage.create_custom(ui.db, OWNER, time_local="07:30", mode="today")
    sch.schedule_sub(ui.jq, sub, KYIV)
    click(ui, f"Sv:{sub.id}")
    assert ("🔕 Вимкнути", f"Se:{sub.id}") in buttons(last(ui, "edit"))
    click(ui, f"Se:{sub.id}")
    assert not storage.get_subscription(ui.db, sub.id).enabled
    assert ui.jq.get_jobs_by_name(f"sub:{sub.id}") == ()
    click(ui, f"Se:{sub.id}")
    assert len(ui.jq.get_jobs_by_name(f"sub:{sub.id}")) == 1
    click(ui, f"Sx:{sub.id}")
    assert ("Так, видалити", f"Sxy:{sub.id}") in buttons(last(ui, "edit"))
    click(ui, f"Sxy:{sub.id}")
    assert storage.get_subscription(ui.db, sub.id) is None
    assert ui.jq.get_jobs_by_name(f"sub:{sub.id}") == ()


def test_sub_card_foreign_id(ui) -> None:
    storage.upsert_user(ui.db, STRANGER)
    foreign = storage.create_custom(ui.db, STRANGER, time_local="07:30", mode="today")
    for verb in ("Sv", "Se", "Sx", "Sxy"):
        click(ui, f"{verb}:{foreign.id}")
    assert storage.get_subscription(ui.db, foreign.id).enabled
    assert [e for e in ui.log if e[0] == "answer"].count(("answer", "Не знайдено")) == 4


def test_location_delete_unschedules(ui) -> None:
    stryi = storage.add_location(ui.db, OWNER, "Стрий", "stryi")
    sub = storage.create_custom(ui.db, OWNER, time_local="07:30", mode="today", location_id=stryi.id)
    sch.schedule_sub(ui.jq, sub, KYIV)
    click(ui, f"Lry:{stryi.id}")
    assert ui.jq.get_jobs_by_name(f"sub:{sub.id}") == ()
