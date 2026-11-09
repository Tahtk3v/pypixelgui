import base64

import pytest

from pypixelgui.commands import CommandError, to_client_call, validate


def test_text_is_cleaned():
    out = validate("send_text", {"text": "hi", "color": "#FF8800", "speed": "40", "animation": "Blink"})
    assert out == {"text": "hi", "color": "ff8800", "speed": 40, "animation": "blink"}


@pytest.mark.parametrize("name,params", [
    ("send_text", {}),                                  # missing text
    ("send_text", {"text": "x", "color": "red"}),       # bad colour
    ("set_brightness", {"level": 101}),
    ("set_brightness", {"level": True}),
    ("set_brightness", {"level": 5, "extra": 1}),       # unknown param
    ("nope", {}),
])
def test_rejects_bad_input(name, params):
    with pytest.raises(CommandError):
        validate(name, params)


def test_remote_restrictions():
    with pytest.raises(CommandError):
        validate("send_image", {"path": "/etc/passwd"}, remote=True)
    with pytest.raises(CommandError):
        validate("send_text", {"text": "x", "font": "/some/font.ttf"}, remote=True)
    with pytest.raises(CommandError):
        validate("clear", {}, remote=True, allow_destructive=False)
    assert validate("clear", {}, remote=True, allow_destructive=True) == {}


def test_image_b64_translates_to_hex():
    data = base64.b64encode(b"\x89PNG").decode()
    kwargs = validate("send_image_b64", {"data": data, "ext": ".png"}, remote=True)
    method, call = to_client_call("send_image_b64", kwargs)
    assert method == "send_image_hex"
    assert call == {"hex_string": "89504e47", "file_extension": ".png"}
