import base64
import io

import pytest
from PIL import Image, ImageSequence

from pypixelgui import presets, render


def _colours(img):
    return {c for _n, c in img.getcolors(1 << 16)}


def _frames(step):
    cmd, params = step
    assert cmd == "send_image_b64" and params["ext"] == ".gif"
    im = Image.open(io.BytesIO(base64.b64decode(params["data"])))
    return [f.convert("RGB") for f in ImageSequence.Iterator(im)]


@pytest.mark.parametrize("size", [(32, 16), (64, 16), (64, 64), (448, 32)])
def test_cyber_flashes_whole_panel_then_shows_text(size):
    (step,) = presets.find("CYBER").steps(*size)
    frames = _frames(step)
    assert all(f.size == size for f in frames)
    yellow = Image.new("RGB", size, (255, 255, 0))
    assert frames[0].tobytes() == yellow.tobytes()          # every LED on
    assert frames[1].getbbox() is None                      # all off
    text_frames = frames[6:]
    assert text_frames and any(f.getbbox() for f in text_frames)
    assert all(_colours(f) <= {(0, 0, 0), (255, 255, 0)} for f in text_frames)


@pytest.mark.parametrize("size", [(32, 16), (96, 16), (64, 32)])
def test_rainbow_welcome_is_coloured_and_animated(size):
    (step,) = presets.find("Rainbow welcome").steps(*size)
    frames = _frames(step)
    assert len(frames) > 1
    colours = set().union(*(_colours(f) for f in frames)) - {(0, 0, 0)}
    assert len(colours) > 6


def test_text_never_shrinks_below_readable_size():
    mask, fits = render.fit_text("A long message here", 32, 16)
    assert not fits and mask.height >= 10  # scrolls instead of shrinking
