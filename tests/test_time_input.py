"""Time input parsing for the custom-subscription wizard."""

import pytest

from weather_bot.bot.subscriptions import is_dst_hour, parse_time_input


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("7:30", "07:30"),
        ("07.30", "07:30"),
        ("0730", "07:30"),
        ("730", "07:30"),
        ("7 30", "07:30"),
        (" 23:59 ", "23:59"),
        ("00:00", "00:00"),
        ("20:05", "20:05"),
    ],
)
def test_accepted(raw: str, expected: str) -> None:
    assert parse_time_input(raw) == expected


@pytest.mark.parametrize("raw", ["24:00", "7:60", "abc", "", "12:3", "1:2:3", "-1:00", "2400"])
def test_rejected(raw: str) -> None:
    assert parse_time_input(raw) is None


@pytest.mark.parametrize("raw", ["03:15", "3:00", "0359"])
def test_dst_hour_is_banned(raw: str) -> None:
    t = parse_time_input(raw)
    assert t is not None and is_dst_hour(t)


@pytest.mark.parametrize("t", ["02:59", "04:00", "13:00", "23:03"])
def test_other_hours_allowed(t: str) -> None:
    assert not is_dst_hour(t)
