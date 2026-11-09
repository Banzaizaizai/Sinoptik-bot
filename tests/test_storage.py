"""Tests for weather_bot.bot.storage (SQLite, tmp_path, no network)."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from weather_bot.bot import storage as st

CHAT = 114665985
OTHER = 222


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "data" / "bot.sqlite3"
    st.migrate(path)
    st.upsert_user(path, CHAT)
    return path


# --- migrations / connection ------------------------------------------------


def test_migrate_idempotent_and_sets_version(tmp_path: Path) -> None:
    path = tmp_path / "bot.sqlite3"
    assert st.migrate(path) == 1
    assert st.migrate(path) == 1
    assert st.get_user_version(path) == 1
    # Fresh DB: no backup needed.
    assert not list(tmp_path.glob("*.bak-v*"))


def test_migrate_backs_up_existing_db(tmp_path: Path) -> None:
    path = tmp_path / "bot.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE junk(x)")
    conn.commit()
    conn.close()
    st.migrate(path)
    assert (tmp_path / "bot.sqlite3.bak-v0").exists()


def test_migrate_rejects_newer_schema(tmp_path: Path) -> None:
    path = tmp_path / "bot.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version=99")
    conn.close()
    with pytest.raises(st.StorageError):
        st.migrate(path)


def test_connection_pragmas(db: Path) -> None:
    conn = st.connect(db)
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()


# --- users / seed -------------------------------------------------------------


def test_upsert_user_idempotent(db: Path) -> None:
    u1 = st.get_user(db, CHAT)
    u2 = st.upsert_user(db, CHAT)
    assert u1 == u2


def test_seed_default_drohobych(db: Path) -> None:
    loc = st.seed_default(db, CHAT)
    assert loc is not None
    assert (loc.title, loc.slug, loc.is_favorite) == ("Дрогобич", "drohobych", True)
    assert st.get_default_location(db, CHAT) == loc
    # Second seed is a no-op.
    assert st.seed_default(db, CHAT) is None
    assert len(st.list_locations(db, CHAT)) == 1


# --- locations ----------------------------------------------------------------


def test_first_location_becomes_default(db: Path) -> None:
    a = st.add_location(db, CHAT, "Стрий", "stryi")
    b = st.add_location(db, CHAT, "Борислав", "boryslav")
    assert st.get_default_location(db, CHAT) == a
    st.set_default(db, CHAT, b.id)
    assert st.get_default_location(db, CHAT) == b
    c = st.add_location(db, CHAT, "Львів", "lviv", make_default=True)
    assert st.get_default_location(db, CHAT) == c


def test_add_location_duplicate_and_limit(db: Path) -> None:
    st.add_location(db, CHAT, "Стрий", "stryi")
    with pytest.raises(st.DuplicateError):
        st.add_location(db, CHAT, "Стрий", "stryi")
    # Same slug is fine for another chat.
    st.add_location(db, OTHER, "Стрий", "stryi")
    for i in range(st.MAX_LOCATIONS - 1):
        st.add_location(db, CHAT, f"L{i}", f"l{i}")
    with pytest.raises(st.LimitReached):
        st.add_location(db, CHAT, "Extra", "extra")


def test_delete_default_promotes_oldest(db: Path) -> None:
    a = st.add_location(db, CHAT, "A", "a")
    b = st.add_location(db, CHAT, "B", "b")
    st.add_location(db, CHAT, "C", "c")
    assert st.delete_location(db, CHAT, a.id) == []
    assert st.get_default_location(db, CHAT) == b
    # Deleting a non-default leaves the default alone.
    st.delete_location(db, CHAT, st.list_locations(db, CHAT)[-1].id)
    assert st.get_default_location(db, CHAT) == b
    st.delete_location(db, CHAT, b.id)
    assert st.get_default_location(db, CHAT) is None
    assert st.get_user(db, CHAT).default_location_id is None  # type: ignore[union-attr]


def test_delete_location_cascades_subscriptions(db: Path) -> None:
    a = st.add_location(db, CHAT, "A", "a")
    s1 = st.create_custom(db, CHAT, time_local="07:30", mode="today", location_id=a.id)
    s2 = st.create_custom(db, CHAT, time_local="09:00", mode="week", location_id=a.id)
    s3 = st.create_custom(db, CHAT, time_local="07:30", mode="today")  # follows default
    assert st.count_location_subscriptions(db, a.id) == 2
    assert st.delete_location(db, CHAT, a.id) == [s1.id, s2.id]
    assert st.get_subscription(db, s1.id) is None
    assert st.get_subscription(db, s2.id) is None
    assert st.get_subscription(db, s3.id) is not None


def test_foreign_chat_cannot_touch_location(db: Path) -> None:
    a = st.add_location(db, CHAT, "A", "a")
    st.upsert_user(db, OTHER)
    assert st.get_location(db, a.id, chat_id=OTHER) is None
    assert st.get_location(db, a.id, chat_id=CHAT) == a
    for fn in (st.delete_location, st.set_default, st.toggle_favorite):
        with pytest.raises(st.NotFound):
            fn(db, OTHER, a.id)


def test_find_location_by_title_case_insensitive(db: Path) -> None:
    a = st.add_location(db, CHAT, "Стрий", "stryi")
    assert st.find_location_by_title(db, CHAT, "  стрий ") == a
    assert st.find_location_by_title(db, CHAT, "Львів") is None


# --- favorites ------------------------------------------------------------------


def test_toggle_favorite(db: Path) -> None:
    a = st.add_location(db, CHAT, "A", "a")
    assert a.is_favorite is False
    assert st.toggle_favorite(db, CHAT, a.id) is True
    assert st.get_location(db, a.id).is_favorite is True  # type: ignore[union-attr]
    assert st.toggle_favorite(db, CHAT, a.id) is False


def test_list_locations_favorites_first(db: Path) -> None:
    a = st.add_location(db, CHAT, "A", "a")
    b = st.add_location(db, CHAT, "B", "b")
    c = st.add_location(db, CHAT, "C", "c")
    d = st.add_location(db, CHAT, "D", "d")
    st.toggle_favorite(db, CHAT, d.id)
    st.toggle_favorite(db, CHAT, b.id)
    assert [x.id for x in st.list_locations(db, CHAT)] == [b.id, d.id, a.id, c.id]


def test_list_favorites_limit_and_order(db: Path) -> None:
    locs = [st.add_location(db, CHAT, f"L{i}", f"l{i}") for i in range(6)]
    for loc in locs:
        st.toggle_favorite(db, CHAT, loc.id)
    favs = st.list_favorites(db, CHAT, limit=4)
    assert [f.id for f in favs] == [loc.id for loc in locs[:4]]
    assert st.list_favorites(db, OTHER) == []


def test_seeded_location_is_favorite(db: Path) -> None:
    st.seed_default(db, CHAT)
    favs = st.list_favorites(db, CHAT)
    assert [f.slug for f in favs] == ["drohobych"]


# --- subscriptions ----------------------------------------------------------------


def test_toggle_preset_create_then_flip(db: Path) -> None:
    s = st.toggle_preset(db, CHAT, "morning")
    assert (s.kind, s.time_local, s.mode, s.enabled, s.location_id) == (
        "preset", "08:00", "today", True, None)
    s2 = st.toggle_preset(db, CHAT, "morning")
    assert s2.id == s.id and s2.enabled is False
    assert st.toggle_preset(db, CHAT, "morning").enabled is True
    ev = st.toggle_preset(db, CHAT, "evening")
    assert (ev.time_local, ev.mode) == ("20:00", "tomorrow")
    assert st.toggle_preset(db, CHAT, "midday").time_local == "13:00"
    with pytest.raises(ValueError):
        st.toggle_preset(db, CHAT, "night")


def test_preset_and_weekly_unique_indexes(db: Path) -> None:
    st.toggle_preset(db, CHAT, "morning")
    st.ensure_weekly(db, CHAT)
    conn = st.connect(db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO subscriptions(chat_id, kind, preset, time_local, mode, created_at)"
                " VALUES(?, 'preset', 'morning', '08:00', 'today', 'x')", (CHAT,))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO subscriptions(chat_id, kind, time_local, mode, created_at)"
                " VALUES(?, 'weekly', '19:00', 'week', 'x')", (CHAT,))
        # Custom rows are not constrained by those indexes.
        conn.execute(
            "INSERT INTO subscriptions(chat_id, kind, time_local, mode, created_at)"
            " VALUES(?, 'custom', '08:00', 'today', 'x')", (CHAT,))
    finally:
        conn.close()


def test_schema_checks_reject_bad_values(db: Path) -> None:
    conn = st.connect(db)
    try:
        for time_local, mode, mask in (("8:00", "today", 1), ("08:00", "month", 1),
                                       ("08:00", "today", 0), ("08:00", "today", 128)):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO subscriptions(chat_id, kind, time_local, mode, days_mask,"
                    " created_at) VALUES(?, 'custom', ?, ?, ?, 'x')",
                    (CHAT, time_local, mode, mask))
    finally:
        conn.close()


def test_weekly_defaults_and_updates(db: Path) -> None:
    assert st.get_weekly(db, CHAT) is None
    w = st.ensure_weekly(db, CHAT)
    assert (w.mode, w.days_mask, w.time_local, w.enabled, w.location_id) == (
        "week", 64, "19:00", False, None)
    assert st.toggle_weekly(db, CHAT).enabled is True
    assert st.set_weekly_day(db, CHAT, 0).days_mask == 1
    assert st.set_weekly_time(db, CHAT, "09:00").time_local == "09:00"
    assert st.toggle_weekly(db, CHAT).enabled is False
    assert st.get_weekly(db, CHAT).id == w.id  # type: ignore[union-attr]
    with pytest.raises(ValueError):
        st.set_weekly_day(db, CHAT, 7)
    with pytest.raises(ValueError):
        st.set_weekly_time(db, CHAT, "24:00")


def test_toggle_weekly_first_time_enables(db: Path) -> None:
    assert st.toggle_weekly(db, CHAT).enabled is True


def test_custom_duplicate_and_limit(db: Path) -> None:
    a = st.add_location(db, CHAT, "A", "a")
    st.create_custom(db, CHAT, time_local="07:30", mode="today", days_mask=31)
    with pytest.raises(st.DuplicateError):
        st.create_custom(db, CHAT, time_local="07:30", mode="today", days_mask=31)
    # Differs by location -> allowed.
    st.create_custom(db, CHAT, time_local="07:30", mode="today", days_mask=31, location_id=a.id)
    with pytest.raises(st.NotFound):
        st.create_custom(db, CHAT, time_local="07:30", mode="today", location_id=999)
    for i in range(st.MAX_SUBSCRIPTIONS - 2):
        st.create_custom(db, CHAT, time_local=f"{i:02d}:15", mode="week")
    with pytest.raises(st.LimitReached):
        st.create_custom(db, CHAT, time_local="23:45", mode="today")


def test_custom_validation(db: Path) -> None:
    with pytest.raises(ValueError):
        st.create_custom(db, CHAT, time_local="7:30", mode="today")
    with pytest.raises(ValueError):
        st.create_custom(db, CHAT, time_local="07:30", mode="month")
    with pytest.raises(ValueError):
        st.create_custom(db, CHAT, time_local="07:30", mode="today", days_mask=0)


def test_enable_disable_delete_subscription(db: Path) -> None:
    s = st.create_custom(db, CHAT, time_local="07:30", mode="today")
    assert st.set_subscription_enabled(db, CHAT, s.id, False).enabled is False
    assert st.list_enabled_subscriptions(db) == []
    assert st.set_subscription_enabled(db, CHAT, s.id, True).enabled is True
    assert [x.id for x in st.list_enabled_subscriptions(db)] == [s.id]
    with pytest.raises(st.NotFound):
        st.set_subscription_enabled(db, OTHER, s.id, False)
    assert st.get_subscription(db, s.id, chat_id=OTHER) is None
    with pytest.raises(st.NotFound):
        st.delete_subscription(db, OTHER, s.id)
    st.delete_subscription(db, CHAT, s.id)
    assert st.list_subscriptions(db, CHAT) == []


def test_disable_all_subscriptions(db: Path) -> None:
    s1 = st.toggle_preset(db, CHAT, "morning")
    s2 = st.create_custom(db, CHAT, time_local="07:30", mode="today")
    assert st.disable_all_subscriptions(db, CHAT) == [s1.id, s2.id]
    assert all(not s.enabled for s in st.list_subscriptions(db, CHAT))


def test_user_delete_cascades(db: Path) -> None:
    st.seed_default(db, CHAT)
    st.toggle_preset(db, CHAT, "morning")
    conn = st.connect(db)
    try:
        conn.execute("DELETE FROM users WHERE chat_id=?", (CHAT,))
    finally:
        conn.close()
    assert st.list_locations(db, CHAT) == []
    assert st.list_subscriptions(db, CHAT) == []


# --- send_log ------------------------------------------------------------------------


def test_send_log_insert_or_ignore(db: Path) -> None:
    s = st.toggle_preset(db, CHAT, "morning")
    d = date(2026, 11, 9)
    assert st.try_start_send(db, s.id, d) is True
    assert st.try_start_send(db, s.id, d) is False
    assert st.try_start_send(db, s.id, "2026-11-10") is True
    assert st.get_send_log(db, s.id, d).status == "sending"  # type: ignore[union-attr]
    st.mark_send(db, s.id, d, "sent")
    e = st.get_send_log(db, s.id, d)
    assert e is not None and e.status == "sent" and e.sent_at is not None
    st.mark_send(db, s.id, "2026-11-10", "failed", error="boom")
    f = st.get_send_log(db, s.id, "2026-11-10")
    assert f is not None and (f.status, f.error, f.sent_at) == ("failed", "boom", None)
    with pytest.raises(ValueError):
        st.mark_send(db, s.id, d, "weird")


def test_prune_send_log(db: Path) -> None:
    today = date(2026, 11, 9)
    st.try_start_send(db, 1, "2026-09-01")
    st.try_start_send(db, 1, "2026-10-10")  # exactly 30 days old: kept
    st.try_start_send(db, 1, "2026-11-09")
    assert st.prune_send_log(db, today) == 1
    assert st.get_send_log(db, 1, "2026-09-01") is None
    assert st.get_send_log(db, 1, "2026-10-10") is not None
