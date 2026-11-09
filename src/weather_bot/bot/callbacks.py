"""callback_data encoding/decoding: ``<verb>[:<arg>...]``, ASCII only, <= 64 bytes.

Every verb has a fixed argument signature (see ``VERBS``). ``encode`` validates
the arguments and asserts the 64-byte Telegram limit; ``decode`` returns a
:class:`Callback` or raises :class:`ValueError` for unknown / malformed data
(the router answers those with "Меню застаріло").

Search candidates are never put into callback_data: ``Lc:<idx>`` only carries
an index into ``user_data["cands"]``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal

MAX_BYTES: Final = 64

# Short forecast-mode codes used in callback_data <-> service modes.
MODE_CODES: Final[dict[str, str]] = {"t": "today", "m": "tomorrow", "w": "week", "x": "10days"}
MODE_TO_CODE: Final[dict[str, str]] = {v: k for k, v in MODE_CODES.items()}

PRESET_CODES: Final[dict[str, str]] = {"mo": "morning", "mi": "midday", "ev": "evening"}
PRESET_TO_CODE: Final[dict[str, str]] = {v: k for k, v in PRESET_CODES.items()}

ArgKind = Literal["id", "mode", "preset", "dow", "hhmm", "mask"]

# verb -> argument kinds. "id" = non-negative int (0 means "default location"
# for f/Scl; an index for Lc/Lcd).
VERBS: Final[dict[str, tuple[ArgKind, ...]]] = {
    "m": (),                      # main menu
    "x": (),                      # cancel
    "f": ("mode", "id"),          # forecast <mode> for location (0 = default)
    "fl": ("mode",),              # pick a location, then forecast <mode>
    "L": (),                      # locations list
    "Lv": ("id",),                # location card
    "Ld": ("id",),                # make default
    "Lf": ("id",),                # toggle favorite
    "Lr": ("id",),                # delete (ask)
    "Lry": ("id",),               # delete (confirmed)
    "La": (),                     # add location (prompt)
    "Lc": ("id",),                # pick candidate idx
    "Lcd": ("id",),               # pick candidate idx and make default
    "S": (),                      # subscriptions screen
    "Sp": ("preset",),            # toggle preset
    "Sw": (),                     # weekly screen
    "Swd": ("dow",),              # weekly: set day (Mon-based 0..6)
    "Swt": ("hhmm",),             # weekly: set time
    "Swo": (),                    # weekly: toggle on/off
    "Sv": ("id",),                # subscription card
    "Se": ("id",),                # enable/disable
    "Sx": ("id",),                # delete (ask)
    "Sxy": ("id",),               # delete (confirmed)
    "Sc": (),                     # custom wizard start
    "Sct": ("hhmm",),             # wizard: time
    "Scm": ("mode",),             # wizard: mode
    "Scd": ("mask",),             # wizard: days mask
    "Scl": ("id",),               # wizard: location (0 = follow default)
}

_ID_RE = re.compile(r"^(0|[1-9]\d{0,17})$")
_HHMM_RE = re.compile(r"^([01]\d|2[0-3])([0-5]\d)$")

Arg = int | str


@dataclass(frozen=True, slots=True)
class Callback:
    verb: str
    args: tuple[Arg, ...] = ()

    def arg(self, i: int = 0) -> Arg:
        return self.args[i]

    def int_arg(self, i: int = 0) -> int:
        v = self.args[i]
        assert isinstance(v, int)
        return v

    def str_arg(self, i: int = 0) -> str:
        v = self.args[i]
        assert isinstance(v, str)
        return v


def _check_arg(kind: ArgKind, value: Arg) -> str:
    """Validate a python value for ``kind``; return its wire form."""
    if kind == "id":
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"bad id {value!r}")
        return str(value)
    if kind == "mode":
        if value in MODE_CODES:
            return str(value)
        if value in MODE_TO_CODE:
            return MODE_TO_CODE[str(value)]
        raise ValueError(f"bad mode {value!r}")
    if kind == "preset":
        if value in PRESET_CODES:
            return str(value)
        if value in PRESET_TO_CODE:
            return PRESET_TO_CODE[str(value)]
        raise ValueError(f"bad preset {value!r}")
    if kind == "dow":
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 6:
            raise ValueError(f"bad weekday {value!r}")
        return str(value)
    if kind == "hhmm":
        s = str(value).replace(":", "")
        if not _HHMM_RE.match(s):
            raise ValueError(f"bad time {value!r}")
        return s
    if kind == "mask":
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 127:
            raise ValueError(f"bad days mask {value!r}")
        return str(value)
    raise ValueError(f"unknown arg kind {kind}")  # pragma: no cover


def encode(verb: str, *args: Arg) -> str:
    """Build callback_data. Modes/presets may be given as codes or full names
    (``"today"``/``"t"``); times as ``"07:30"`` or ``"0730"``."""
    kinds = VERBS.get(verb)
    if kinds is None:
        raise ValueError(f"unknown verb {verb!r}")
    if len(args) != len(kinds):
        raise ValueError(f"{verb} expects {len(kinds)} args, got {len(args)}")
    data = ":".join([verb, *(_check_arg(k, a) for k, a in zip(kinds, args))])
    assert len(data.encode("ascii")) <= MAX_BYTES, data
    return data


def _parse_arg(kind: ArgKind, raw: str) -> Arg:
    if kind in ("id", "dow", "mask"):
        if not _ID_RE.match(raw):
            raise ValueError(f"bad int {raw!r}")
        value = int(raw)
        _check_arg(kind, value)
        return value
    if kind == "mode":
        if raw not in MODE_CODES:
            raise ValueError(f"bad mode {raw!r}")
        return MODE_CODES[raw]
    if kind == "preset":
        if raw not in PRESET_CODES:
            raise ValueError(f"bad preset {raw!r}")
        return PRESET_CODES[raw]
    if kind == "hhmm":
        if not _HHMM_RE.match(raw):
            raise ValueError(f"bad time {raw!r}")
        return f"{raw[:2]}:{raw[2:]}"
    raise ValueError(f"unknown arg kind {kind}")  # pragma: no cover


def decode(data: str | None) -> Callback:
    """Parse callback_data. Modes/presets decode to full names
    (``"today"``, ``"morning"``), times to ``"HH:MM"``, ints to ``int``."""
    if not data or len(data.encode("utf-8")) > MAX_BYTES:
        raise ValueError("empty or oversized callback data")
    verb, *raw_args = data.split(":")
    kinds = VERBS.get(verb)
    if kinds is None:
        raise ValueError(f"unknown verb {verb!r}")
    if len(raw_args) != len(kinds):
        raise ValueError(f"{verb}: wrong arg count")
    return Callback(verb, tuple(_parse_arg(k, r) for k, r in zip(kinds, raw_args)))
