import pytest

from weather_bot.config import BotConfig


def test_botconfig_requires_allowed_ids(monkeypatch):
    monkeypatch.setattr("weather_bot.config.load_dotenv", lambda: None)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:x")
    monkeypatch.setenv("ALLOWED_CHAT_IDS", "")
    with pytest.raises(RuntimeError):
        BotConfig.load()
    monkeypatch.setenv("ALLOWED_CHAT_IDS", "114665985, 5,114665985")
    monkeypatch.delenv("BOT_DB_PATH", raising=False)
    cfg = BotConfig.load()
    assert cfg.allowed_chat_ids == (114665985, 5)
    assert cfg.owner_id == 114665985
    assert cfg.db_path.is_absolute() and cfg.db_path.name == "bot.sqlite3"
    monkeypatch.setenv("ALLOWED_CHAT_IDS", "abc")
    with pytest.raises(RuntimeError):
        BotConfig.load()


def test_build_application_offline(tmp_path):
    from weather_bot.bot.app import build_application

    cfg = BotConfig("1:x", (1,), tmp_path / "b.sqlite3")
    app = build_application(cfg)
    assert app.bot_data["cfg"] is cfg
    assert app.job_queue is not None
    assert 99 in app.handlers
