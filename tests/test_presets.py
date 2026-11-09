import base64
import io

import pytest
from PIL import Image

from pypixelgui import presets
from pypixelgui.commands import validate


@pytest.mark.parametrize("preset", presets.BUILTIN, ids=lambda p: p.name)
@pytest.mark.parametrize("size", [(32, 16), (64, 64), (448, 32)])
def test_every_builtin_validates(preset, size):
    for command, params in preset.steps(*size):
        validate(command, params, remote=True, allow_destructive=False)


def test_flag_is_exact_size_and_correct_colours():
    (cmd, params), = presets.find("Trans flag").steps(32, 16)
    img = Image.open(io.BytesIO(base64.b64decode(params["data"]))).convert("RGB")
    assert img.size == (32, 16)
    assert img.getpixel((0, 0)) == (0x5b, 0xce, 0xfa)
    assert img.getpixel((0, 8)) == (0xff, 0xff, 0xff)


def test_user_preset_roundtrip():
    entry = {"name": "Mine", "steps": [["set_brightness", {"level": 10}]]}
    p = presets.find("mine", [entry])
    assert p.category == "My presets" and p.steps(1, 1) == [("set_brightness", {"level": 10})]
