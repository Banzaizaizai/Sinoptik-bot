import asyncio
from types import SimpleNamespace

from weather_bot.bot.access import is_allowed, owner_filter, owner_only

OWNER = 114665985


def _update(chat_id, chat_type="private", with_query=True):
    answered = []

    async def answer(*a, **k):
        answered.append(a)

    q = SimpleNamespace(answer=answer) if with_query else None
    upd = SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id, type=chat_type), callback_query=q)
    return upd, answered


def _ctx():
    return SimpleNamespace(bot_data={"cfg": SimpleNamespace(allowed_chat_ids=(OWNER,))})


def test_is_allowed():
    assert is_allowed(_update(OWNER)[0], [OWNER])
    assert not is_allowed(_update(OWNER, "group")[0], [OWNER])
    assert not is_allowed(_update(OWNER, "supergroup")[0], [OWNER])
    assert not is_allowed(_update(42)[0], [OWNER])
    assert not is_allowed(SimpleNamespace(effective_chat=None), [OWNER])


def test_owner_only_decorator():
    calls = []

    @owner_only
    async def handler(update, context):
        calls.append(update)
        return "ok"

    upd, answered = _update(OWNER)
    assert asyncio.run(handler(upd, _ctx())) == "ok"
    for bad in (_update(OWNER, "group"), _update(777)):
        assert asyncio.run(handler(bad[0], _ctx())) is None
        assert bad[1] == [()]  # silent answer
    assert calls == [upd]


def test_owner_filter_ptb():
    from telegram import Chat, Message, Update, User
    from datetime import datetime, timezone

    f = owner_filter([OWNER])

    def msg(chat_id, ctype):
        m = Message(1, datetime.now(timezone.utc), Chat(chat_id, ctype),
                    from_user=User(chat_id, "u", False), text="hi")
        return Update(1, message=m)

    assert f.check_update(msg(OWNER, "private"))
    assert not f.check_update(msg(OWNER, "group"))
    assert not f.check_update(msg(5, "private"))
