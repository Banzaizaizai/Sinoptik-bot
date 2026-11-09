import pytest

from weather_bot.bot.callbacks import MAX_BYTES, VERBS, Callback, decode, encode

BIG = 2**53

SAMPLES = {
    "id": [0, 1, BIG], "mode": ["today", "tomorrow", "week", "10days"],
    "preset": ["morning", "midday", "evening"], "dow": [0, 6],
    "hhmm": ["00:00", "07:30", "23:59"], "mask": [1, 31, 127],
}


def _arg_sets(kinds):
    if not kinds:
        return [()]
    return [(a, *rest) for a in SAMPLES[kinds[0]] for rest in _arg_sets(kinds[1:])]


@pytest.mark.parametrize("verb", sorted(VERBS))
def test_every_encoder_fits_and_roundtrips(verb):
    for args in _arg_sets(VERBS[verb]):
        data = encode(verb, *args)
        assert len(data.encode("ascii")) <= MAX_BYTES
        assert decode(data) == Callback(verb, tuple(args))


def test_codes_are_accepted_by_encoder():
    assert encode("f", "t", 0) == "f:t:0"
    assert encode("Sp", "mo") == "Sp:mo"
    assert encode("Swt", "0730") == "Swt:0730"
    assert decode("Swt:0730").str_arg() == "07:30"


@pytest.mark.parametrize("data", [
    None, "", "zz", "m:1", "f:t", "f:q:1", "f:t:-1", "f:t:01", "Lv:abc", "Lv:1.5",
    "Sp:xx", "Swd:7", "Swt:2400", "Swt:0760", "Scd:0", "Scd:128", "L:", "Lv:" + "9" * 70,
])
def test_decode_rejects(data):
    with pytest.raises(ValueError):
        decode(data)


@pytest.mark.parametrize("verb,args", [
    ("zz", ()), ("Lv", ()), ("Lv", (-1,)), ("Lv", (True,)), ("f", ("bad", 0)),
    ("Swd", (7,)), ("Swt", ("24:00",)), ("Scd", (0,)),
])
def test_encode_rejects(verb, args):
    with pytest.raises(ValueError):
        encode(verb, *args)


def test_persistent_menu_keyboard():
    from weather_bot.bot import keyboards, texts

    markup = keyboards.persistent_menu()
    assert markup.keyboard[0][0].text == texts.MENU_BUTTON
    assert markup.is_persistent and markup.resize_keyboard
