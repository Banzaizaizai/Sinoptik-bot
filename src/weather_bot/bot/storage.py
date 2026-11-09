"""SQLite storage for the interactive bot: schema, migrations and CRUD.

Design notes:
- Pure synchronous ``sqlite3``; async callers wrap calls in ``asyncio.to_thread``.
- One short-lived connection per call (``_tx``), explicit ``BEGIN IMMEDIATE``
  transactions, ``foreign_keys=ON`` and ``busy_timeout=5000`` on every
  connection, ``journal_mode=WAL`` set once during migration.
- The database is the only source of truth; the scheduler is rebuilt from it.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import re
from typing import Final, Literal

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_LOCATIONS: Final = 10
MAX_SUBSCRIPTIONS: Final = 20
SEND_LOG_RETENTION_DAYS: Final = 30

DEFAULT_SEED_TITLE: Final = "Дрогобич"
DEFAULT_SEED_SLUG: Final = "drohobych"
DEFAULT_SEED_DESCRIPTION: Final = "Львівська область"
DEFAULT_SEED_LAT: Final = 49.3489
DEFAULT_SEED_LNG: Final = 23.5069

Preset = Literal["morning", "midday", "evening"]
Mode = Literal["today", "tomorrow", "week", "10days"]
SendStatus = Literal["sending", "sent", "failed", "noloc"]

# preset -> (time_local, mode)
PRESETS: Final[dict[str, tuple[str, str]]] = {
    "morning": ("08:00", "today"),
    "midday": ("13:00", "today"),
    "evening": ("20:00", "tomorrow"),
}
MODES: Final = frozenset({"today", "tomorrow", "week", "10days"})
SEND_STATUSES: Final = frozenset({"sending", "sent", "failed", "noloc"})

WEEKLY_DEFAULT_MASK: Final = 64  # bit6 = Sunday
WEEKLY_DEFAULT_TIME: Final = "19:00"
ALL_DAYS_MASK: Final = 127

# ---------------------------------------------------------------------------
# Schema / migrations
# ---------------------------------------------------------------------------

_V1_SQL: Final = """
CREATE TABLE users(chat_id INTEGER PRIMARY KEY, created_at TEXT NOT NULL,
  default_location_id INTEGER REFERENCES locations(id) ON DELETE SET NULL);
CREATE TABLE locations(id INTEGER PRIMARY KEY,
  chat_id INTEGER NOT NULL REFERENCES users(chat_id) ON DELETE CASCADE,
  title TEXT NOT NULL, description TEXT, slug TEXT NOT NULL,
  lat REAL, lng REAL, source TEXT NOT NULL DEFAULT 'sinoptik', created_at TEXT NOT NULL,
  is_favorite INTEGER NOT NULL DEFAULT 0,
  UNIQUE(chat_id, slug));
CREATE TABLE subscriptions(id INTEGER PRIMARY KEY,
  chat_id INTEGER NOT NULL REFERENCES users(chat_id) ON DELETE CASCADE,
  location_id INTEGER REFERENCES locations(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK(kind IN ('preset','custom','weekly')),
  preset TEXT CHECK(preset IN ('morning','midday','evening')),
  time_local TEXT NOT NULL CHECK(time_local GLOB '[0-2][0-9]:[0-5][0-9]'),
  mode TEXT NOT NULL CHECK(mode IN ('today','tomorrow','week','10days')),
  days_mask INTEGER NOT NULL DEFAULT 127 CHECK(days_mask BETWEEN 1 AND 127),
  enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE UNIQUE INDEX ux_preset ON subscriptions(chat_id, preset) WHERE kind='preset';
CREATE UNIQUE INDEX ux_weekly ON subscriptions(chat_id) WHERE kind='weekly';
CREATE TABLE send_log(subscription_id INTEGER NOT NULL, fire_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'sending',
  sent_at TEXT, error TEXT, PRIMARY KEY(subscription_id, fire_date));
"""

# MIGRATIONS[i] upgrades user_version i -> i + 1.
MIGRATIONS: Final[list[str]] = [_V1_SQL]
SCHEMA_VERSION: Final = len(MIGRATIONS)


class StorageError(Exception):
    """Base class for domain errors raised by this module."""


class LimitReached(StorageError):
    """Per-user limit (locations / subscriptions) would be exceeded."""


class DuplicateError(StorageError):
    """An equivalent row already exists."""


class NotFound(StorageError):
    """The referenced row does not exist (or belongs to another chat)."""


# ---------------------------------------------------------------------------
# Row dataclasses
# ---------------------------------------------------------------------------


_SLUG_RE: Final = re.compile(r"^[a-z0-9-]+$")


@dataclass(frozen=True, slots=True)
class User:
    chat_id: int
    created_at: str
    default_location_id: int | None


@dataclass(frozen=True, slots=True)
class Location:
    id: int
    chat_id: int
    title: str
    description: str | None
    slug: str
    lat: float | None
    lng: float | None
    source: str
    created_at: str
    is_favorite: bool


@dataclass(frozen=True, slots=True)
class Subscription:
    id: int
    chat_id: int
    location_id: int | None  # None = follow the user's default location
    kind: str
    preset: str | None
    time_local: str
    mode: str
    days_mask: int
    enabled: bool
    created_at: str


@dataclass(frozen=True, slots=True)
class SendLogEntry:
    subscription_id: int
    fire_date: str
    status: str
    sent_at: str | None
    error: str | None


def _user(r: sqlite3.Row) -> User:
    return User(r["chat_id"], r["created_at"], r["default_location_id"])


def _location(r: sqlite3.Row) -> Location:
    return Location(
        id=r["id"], chat_id=r["chat_id"], title=r["title"], description=r["description"],
        slug=r["slug"], lat=r["lat"], lng=r["lng"], source=r["source"],
        created_at=r["created_at"], is_favorite=bool(r["is_favorite"]),
    )


def _subscription(r: sqlite3.Row) -> Subscription:
    return Subscription(
        id=r["id"], chat_id=r["chat_id"], location_id=r["location_id"], kind=r["kind"],
        preset=r["preset"], time_local=r["time_local"], mode=r["mode"],
        days_mask=r["days_mask"], enabled=bool(r["enabled"]), created_at=r["created_at"],
    )


def _send_log(r: sqlite3.Row) -> SendLogEntry:
    return SendLogEntry(r["subscription_id"], r["fire_date"], r["status"], r["sent_at"], r["error"])


def _now() -> str:
    # Microsecond precision keeps created_at ordering stable within one second.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a connection in autocommit mode with the per-connection pragmas."""
    conn = sqlite3.connect(str(db_path), isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def _tx(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    """Short-lived connection wrapped in a single write transaction."""
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        conn.close()


@contextmanager
def _ro(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    """Short-lived connection for reads."""
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def get_user_version(db_path: str | Path) -> int:
    with _ro(db_path) as conn:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(db_path: str | Path) -> int:
    """Bring the database to SCHEMA_VERSION; return the resulting version.

    Idempotent. Before upgrading an existing non-empty database the file is
    copied to ``<db>.bak-v{current}`` via the SQLite backup API (WAL-safe).
    Each migration runs in one transaction together with its user_version bump.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        current = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if current > SCHEMA_VERSION:
            raise StorageError(f"DB schema v{current} is newer than supported v{SCHEMA_VERSION}")
        if current == SCHEMA_VERSION:
            return current

        has_tables = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
        if current > 0 or has_tables:
            bak = path.with_name(f"{path.name}.bak-v{current}")
            dest = sqlite3.connect(str(bak))
            try:
                conn.backup(dest)
            finally:
                dest.close()
            log.info("DB backup written to %s", bak)

        for version in range(current, SCHEMA_VERSION):
            statements = [s.strip() for s in MIGRATIONS[version].split(";") if s.strip()]
            conn.execute("BEGIN IMMEDIATE")
            try:
                for stmt in statements:
                    conn.execute(stmt)
                conn.execute(f"PRAGMA user_version={version + 1}")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
            log.info("DB migrated to v%d", version + 1)
        return SCHEMA_VERSION
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def upsert_user(db_path: str | Path, chat_id: int) -> User:
    with _tx(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(chat_id, created_at) VALUES(?, ?)", (chat_id, _now())
        )
        return _user(conn.execute("SELECT * FROM users WHERE chat_id=?", (chat_id,)).fetchone())


def get_user(db_path: str | Path, chat_id: int) -> User | None:
    with _ro(db_path) as conn:
        r = conn.execute("SELECT * FROM users WHERE chat_id=?", (chat_id,)).fetchone()
        return _user(r) if r else None


def seed_default(db_path: str | Path, chat_id: int) -> Location | None:
    """Give a user without locations Дрогобич as default + favorite.

    Returns the new location, or None if the user already had locations.
    """
    with _tx(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(chat_id, created_at) VALUES(?, ?)", (chat_id, _now())
        )
        if conn.execute("SELECT 1 FROM locations WHERE chat_id=? LIMIT 1", (chat_id,)).fetchone():
            return None
        loc = _insert_location(
            conn, chat_id, DEFAULT_SEED_TITLE, DEFAULT_SEED_SLUG,
            description=DEFAULT_SEED_DESCRIPTION, lat=DEFAULT_SEED_LAT, lng=DEFAULT_SEED_LNG,
            favorite=True,
        )
        conn.execute("UPDATE users SET default_location_id=? WHERE chat_id=?", (loc.id, chat_id))
        return loc


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------


def _insert_location(
    conn: sqlite3.Connection, chat_id: int, title: str, slug: str, *,
    description: str | None, lat: float | None, lng: float | None,
    source: str = "sinoptik", favorite: bool = False,
) -> Location:
    cur = conn.execute(
        "INSERT INTO locations(chat_id, title, description, slug, lat, lng, source,"
        " created_at, is_favorite) VALUES(?,?,?,?,?,?,?,?,?)",
        (chat_id, title, description, slug, lat, lng, source, _now(), int(favorite)),
    )
    return _location(conn.execute("SELECT * FROM locations WHERE id=?", (cur.lastrowid,)).fetchone())


def add_location(
    db_path: str | Path, chat_id: int, title: str, slug: str, *,
    description: str | None = None, lat: float | None = None, lng: float | None = None,
    source: str = "sinoptik", make_default: bool = False,
) -> Location:
    """Add a location. The user's first location becomes the default.

    Raises DuplicateError if the slug is already saved, LimitReached at 10,
    ValueError if the slug is not a plain sinoptik slug.
    """
    if not _SLUG_RE.match(slug):
        raise ValueError(f"invalid slug: {slug!r}")
    with _tx(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(chat_id, created_at) VALUES(?, ?)", (chat_id, _now())
        )
        if conn.execute(
            "SELECT 1 FROM locations WHERE chat_id=? AND slug=?", (chat_id, slug)
        ).fetchone():
            raise DuplicateError(slug)
        count = conn.execute("SELECT COUNT(*) FROM locations WHERE chat_id=?", (chat_id,)).fetchone()[0]
        if count >= MAX_LOCATIONS:
            raise LimitReached(f"locations limit {MAX_LOCATIONS}")
        loc = _insert_location(
            conn, chat_id, title, slug, description=description, lat=lat, lng=lng, source=source
        )
        default_id = conn.execute(
            "SELECT default_location_id FROM users WHERE chat_id=?", (chat_id,)
        ).fetchone()[0]
        if make_default or default_id is None:
            conn.execute("UPDATE users SET default_location_id=? WHERE chat_id=?", (loc.id, chat_id))
        return loc


def get_location(db_path: str | Path, location_id: int, chat_id: int | None = None) -> Location | None:
    """Fetch a location; when chat_id is given, rows of other chats yield None."""
    with _ro(db_path) as conn:
        r = conn.execute("SELECT * FROM locations WHERE id=?", (location_id,)).fetchone()
    if r is None or (chat_id is not None and r["chat_id"] != chat_id):
        return None
    return _location(r)


def get_default_location(db_path: str | Path, chat_id: int) -> Location | None:
    with _ro(db_path) as conn:
        r = conn.execute(
            "SELECT l.* FROM users u JOIN locations l ON l.id=u.default_location_id"
            " WHERE u.chat_id=?", (chat_id,),
        ).fetchone()
        return _location(r) if r else None


def list_locations(db_path: str | Path, chat_id: int) -> list[Location]:
    """All locations of a chat: favorites first, then by creation order."""
    with _ro(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM locations WHERE chat_id=?"
            " ORDER BY is_favorite DESC, created_at, id", (chat_id,),
        ).fetchall()
        return [_location(r) for r in rows]


def list_favorites(db_path: str | Path, chat_id: int, limit: int = 4) -> list[Location]:
    with _ro(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM locations WHERE chat_id=? AND is_favorite=1"
            " ORDER BY created_at, id LIMIT ?", (chat_id, limit),
        ).fetchall()
        return [_location(r) for r in rows]


def find_location_by_title(db_path: str | Path, chat_id: int, title: str) -> Location | None:
    """Case-insensitive title match (Python casefold, so Cyrillic works)."""
    needle = title.strip().casefold()
    for loc in list_locations(db_path, chat_id):
        if loc.title.casefold() == needle:
            return loc
    return None


def set_default(db_path: str | Path, chat_id: int, location_id: int) -> None:
    with _tx(db_path) as conn:
        if not conn.execute(
            "SELECT 1 FROM locations WHERE id=? AND chat_id=?", (location_id, chat_id)
        ).fetchone():
            raise NotFound(f"location {location_id}")
        conn.execute("UPDATE users SET default_location_id=? WHERE chat_id=?", (location_id, chat_id))


def toggle_favorite(db_path: str | Path, chat_id: int, location_id: int) -> bool:
    """Flip is_favorite; return the new value."""
    with _tx(db_path) as conn:
        r = conn.execute(
            "SELECT is_favorite FROM locations WHERE id=? AND chat_id=?", (location_id, chat_id)
        ).fetchone()
        if r is None:
            raise NotFound(f"location {location_id}")
        new = not bool(r[0])
        conn.execute("UPDATE locations SET is_favorite=? WHERE id=?", (int(new), location_id))
        return new


def count_location_subscriptions(db_path: str | Path, location_id: int) -> int:
    with _ro(db_path) as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM subscriptions WHERE location_id=?", (location_id,)
        ).fetchone()[0])


def delete_location(db_path: str | Path, chat_id: int, location_id: int) -> list[int]:
    """Delete a location; cascade its subscriptions; promote a new default.

    Returns the ids of the deleted subscriptions so the caller can unschedule
    them. All in one transaction.
    """
    with _tx(db_path) as conn:
        if not conn.execute(
            "SELECT 1 FROM locations WHERE id=? AND chat_id=?", (location_id, chat_id)
        ).fetchone():
            raise NotFound(f"location {location_id}")
        sub_ids = [r[0] for r in conn.execute(
            "SELECT id FROM subscriptions WHERE location_id=? ORDER BY id", (location_id,)
        )]
        default_id = conn.execute(
            "SELECT default_location_id FROM users WHERE chat_id=?", (chat_id,)
        ).fetchone()[0]
        conn.execute("DELETE FROM locations WHERE id=?", (location_id,))  # FK SET NULL / CASCADE
        if default_id == location_id:
            nxt = conn.execute(
                "SELECT id FROM locations WHERE chat_id=? ORDER BY created_at, id LIMIT 1", (chat_id,)
            ).fetchone()
            conn.execute(
                "UPDATE users SET default_location_id=? WHERE chat_id=?",
                (nxt[0] if nxt else None, chat_id),
            )
        return sub_ids


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------


def _get_sub(conn: sqlite3.Connection, sub_id: int) -> Subscription:
    return _subscription(conn.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone())


def _validate_time(time_local: str) -> None:
    ok = (
        len(time_local) == 5 and time_local[2] == ":"
        and time_local[:2].isdigit() and time_local[3:].isdigit()
        and int(time_local[:2]) < 24 and int(time_local[3:]) < 60
    )
    if not ok:
        raise ValueError(f"bad time_local {time_local!r}")


def get_subscription(db_path: str | Path, sub_id: int, chat_id: int | None = None) -> Subscription | None:
    with _ro(db_path) as conn:
        r = conn.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
    if r is None or (chat_id is not None and r["chat_id"] != chat_id):
        return None
    return _subscription(r)


def list_subscriptions(db_path: str | Path, chat_id: int) -> list[Subscription]:
    with _ro(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM subscriptions WHERE chat_id=? ORDER BY time_local, id", (chat_id,)
        ).fetchall()
        return [_subscription(r) for r in rows]


def list_enabled_subscriptions(db_path: str | Path) -> list[Subscription]:
    """All enabled subscriptions (for rebuild_all / catch-up)."""
    with _ro(db_path) as conn:
        rows = conn.execute("SELECT * FROM subscriptions WHERE enabled=1 ORDER BY id").fetchall()
        return [_subscription(r) for r in rows]


def get_preset(db_path: str | Path, chat_id: int, preset: str) -> Subscription | None:
    with _ro(db_path) as conn:
        r = conn.execute(
            "SELECT * FROM subscriptions WHERE chat_id=? AND kind='preset' AND preset=?",
            (chat_id, preset),
        ).fetchone()
        return _subscription(r) if r else None


def toggle_preset(db_path: str | Path, chat_id: int, preset: str) -> Subscription:
    """Create the preset row enabled on first toggle; afterwards flip enabled."""
    if preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r}")
    time_local, mode = PRESETS[preset]
    with _tx(db_path) as conn:
        r = conn.execute(
            "SELECT id, enabled FROM subscriptions WHERE chat_id=? AND kind='preset' AND preset=?",
            (chat_id, preset),
        ).fetchone()
        if r is None:
            cur = conn.execute(
                "INSERT INTO subscriptions(chat_id, location_id, kind, preset, time_local, mode,"
                " days_mask, enabled, created_at) VALUES(?, NULL, 'preset', ?, ?, ?, ?, 1, ?)",
                (chat_id, preset, time_local, mode, ALL_DAYS_MASK, _now()),
            )
            return _get_sub(conn, int(cur.lastrowid))  # type: ignore[arg-type]
        conn.execute("UPDATE subscriptions SET enabled=? WHERE id=?", (0 if r["enabled"] else 1, r["id"]))
        return _get_sub(conn, r["id"])


def _ensure_weekly(conn: sqlite3.Connection, chat_id: int) -> Subscription:
    r = conn.execute(
        "SELECT * FROM subscriptions WHERE chat_id=? AND kind='weekly'", (chat_id,)
    ).fetchone()
    if r is not None:
        return _subscription(r)
    # Created disabled: only an explicit toggle turns the digest on.
    cur = conn.execute(
        "INSERT INTO subscriptions(chat_id, location_id, kind, preset, time_local, mode,"
        " days_mask, enabled, created_at) VALUES(?, NULL, 'weekly', NULL, ?, 'week', ?, 0, ?)",
        (chat_id, WEEKLY_DEFAULT_TIME, WEEKLY_DEFAULT_MASK, _now()),
    )
    return _get_sub(conn, int(cur.lastrowid))  # type: ignore[arg-type]


def get_weekly(db_path: str | Path, chat_id: int) -> Subscription | None:
    with _ro(db_path) as conn:
        r = conn.execute(
            "SELECT * FROM subscriptions WHERE chat_id=? AND kind='weekly'", (chat_id,)
        ).fetchone()
        return _subscription(r) if r else None


def ensure_weekly(db_path: str | Path, chat_id: int) -> Subscription:
    """Return the weekly digest row, creating it (disabled, Sun 19:00) if absent."""
    with _tx(db_path) as conn:
        return _ensure_weekly(conn, chat_id)


def set_weekly_day(db_path: str | Path, chat_id: int, weekday: int) -> Subscription:
    """Set the digest day; weekday is Mon-based 0..6."""
    if not 0 <= weekday <= 6:
        raise ValueError(f"bad weekday {weekday}")
    with _tx(db_path) as conn:
        sub = _ensure_weekly(conn, chat_id)
        conn.execute("UPDATE subscriptions SET days_mask=? WHERE id=?", (1 << weekday, sub.id))
        return _get_sub(conn, sub.id)


def set_weekly_time(db_path: str | Path, chat_id: int, time_local: str) -> Subscription:
    _validate_time(time_local)
    with _tx(db_path) as conn:
        sub = _ensure_weekly(conn, chat_id)
        conn.execute("UPDATE subscriptions SET time_local=? WHERE id=?", (time_local, sub.id))
        return _get_sub(conn, sub.id)


def toggle_weekly(db_path: str | Path, chat_id: int) -> Subscription:
    with _tx(db_path) as conn:
        sub = _ensure_weekly(conn, chat_id)
        conn.execute("UPDATE subscriptions SET enabled=? WHERE id=?", (0 if sub.enabled else 1, sub.id))
        return _get_sub(conn, sub.id)


def create_custom(
    db_path: str | Path, chat_id: int, *, time_local: str, mode: str,
    days_mask: int = ALL_DAYS_MASK, location_id: int | None = None,
) -> Subscription:
    """Create a custom subscription (location_id None = follow default).

    Raises DuplicateError for an identical custom row, LimitReached at 20
    subscriptions (all kinds), NotFound for a foreign/missing location.
    """
    _validate_time(time_local)
    if mode not in MODES:
        raise ValueError(f"bad mode {mode!r}")
    if not 1 <= days_mask <= ALL_DAYS_MASK:
        raise ValueError(f"bad days_mask {days_mask}")
    with _tx(db_path) as conn:
        if location_id is not None and not conn.execute(
            "SELECT 1 FROM locations WHERE id=? AND chat_id=?", (location_id, chat_id)
        ).fetchone():
            raise NotFound(f"location {location_id}")
        if conn.execute(
            "SELECT 1 FROM subscriptions WHERE chat_id=? AND kind='custom' AND time_local=?"
            " AND mode=? AND days_mask=? AND location_id IS ?",
            (chat_id, time_local, mode, days_mask, location_id),
        ).fetchone():
            raise DuplicateError("subscription")
        count = conn.execute(
            "SELECT COUNT(*) FROM subscriptions WHERE chat_id=?", (chat_id,)
        ).fetchone()[0]
        if count >= MAX_SUBSCRIPTIONS:
            raise LimitReached(f"subscriptions limit {MAX_SUBSCRIPTIONS}")
        cur = conn.execute(
            "INSERT INTO subscriptions(chat_id, location_id, kind, preset, time_local, mode,"
            " days_mask, enabled, created_at) VALUES(?, ?, 'custom', NULL, ?, ?, ?, 1, ?)",
            (chat_id, location_id, time_local, mode, days_mask, _now()),
        )
        return _get_sub(conn, int(cur.lastrowid))  # type: ignore[arg-type]


def set_subscription_enabled(
    db_path: str | Path, chat_id: int, sub_id: int, enabled: bool
) -> Subscription:
    with _tx(db_path) as conn:
        cur = conn.execute(
            "UPDATE subscriptions SET enabled=? WHERE id=? AND chat_id=?",
            (int(enabled), sub_id, chat_id),
        )
        if cur.rowcount == 0:
            raise NotFound(f"subscription {sub_id}")
        return _get_sub(conn, sub_id)


def disable_all_subscriptions(db_path: str | Path, chat_id: int) -> list[int]:
    """Disable every subscription of a chat (e.g. on Forbidden); return their ids."""
    with _tx(db_path) as conn:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM subscriptions WHERE chat_id=? AND enabled=1 ORDER BY id", (chat_id,)
        )]
        conn.execute("UPDATE subscriptions SET enabled=0 WHERE chat_id=?", (chat_id,))
        return ids


def delete_subscription(db_path: str | Path, chat_id: int, sub_id: int) -> None:
    with _tx(db_path) as conn:
        cur = conn.execute("DELETE FROM subscriptions WHERE id=? AND chat_id=?", (sub_id, chat_id))
        if cur.rowcount == 0:
            raise NotFound(f"subscription {sub_id}")


# ---------------------------------------------------------------------------
# send_log
# ---------------------------------------------------------------------------


def try_start_send(db_path: str | Path, sub_id: int, fire_date: date | str) -> bool:
    """INSERT OR IGNORE a 'sending' row. True if this caller owns the send."""
    with _tx(db_path) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO send_log(subscription_id, fire_date, status) VALUES(?, ?, 'sending')",
            (sub_id, str(fire_date)),
        )
        return cur.rowcount == 1


def mark_send(
    db_path: str | Path, sub_id: int, fire_date: date | str, status: str,
    error: str | None = None,
) -> None:
    if status not in SEND_STATUSES:
        raise ValueError(f"bad status {status!r}")
    sent_at = _now() if status == "sent" else None
    with _tx(db_path) as conn:
        conn.execute(
            "UPDATE send_log SET status=?, sent_at=COALESCE(?, sent_at), error=?"
            " WHERE subscription_id=? AND fire_date=?",
            (status, sent_at, error, sub_id, str(fire_date)),
        )


def get_send_log(db_path: str | Path, sub_id: int, fire_date: date | str) -> SendLogEntry | None:
    with _ro(db_path) as conn:
        r = conn.execute(
            "SELECT * FROM send_log WHERE subscription_id=? AND fire_date=?", (sub_id, str(fire_date))
        ).fetchone()
        return _send_log(r) if r else None


def prune_send_log(
    db_path: str | Path, today: date, keep_days: int = SEND_LOG_RETENTION_DAYS
) -> int:
    """Delete rows with fire_date older than ``today - keep_days``; return count."""
    cutoff = (today - timedelta(days=keep_days)).isoformat()
    with _tx(db_path) as conn:
        return conn.execute("DELETE FROM send_log WHERE fire_date < ?", (cutoff,)).rowcount
