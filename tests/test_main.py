from datetime import date, datetime
from zoneinfo import ZoneInfo

from weather_bot.main import FETCHERS, _target_date
from weather_bot.fetcher import fetch_today_html, fetch_tomorrow_html

KYIV = ZoneInfo("Europe/Kyiv")


def test_today_mode_targets_current_date():
    assert _target_date("today", KYIV) == datetime.now(KYIV).date()


def test_tomorrow_mode_targets_next_date():
    assert _target_date("tomorrow", KYIV) == date.fromordinal(
        datetime.now(KYIV).date().toordinal() + 1
    )


def test_fetchers_map_to_expected_functions():
    assert FETCHERS["today"] is fetch_today_html
    assert FETCHERS["tomorrow"] is fetch_tomorrow_html


def test_channel_menu_button(monkeypatch):
    from weather_bot.main import channel_menu_markup

    monkeypatch.delenv("BOT_USERNAME", raising=False)
    url = channel_menu_markup()["inline_keyboard"][0][0]["url"]
    assert url == "https://t.me/mysinoptikweather_bot?start=menu"

    monkeypatch.setenv("BOT_USERNAME", "@other_bot")
    assert channel_menu_markup()["inline_keyboard"][0][0]["url"].startswith("https://t.me/other_bot?")

    monkeypatch.setenv("BOT_USERNAME", "")
    assert channel_menu_markup() is None
