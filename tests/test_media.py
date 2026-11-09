import io
import math

from PIL import Image, ImageDraw, ImageSequence

from pypixelgui import media


def _silhouette_gif(frames=300, size=(360, 270), ms=33) -> bytes:
    out = []
    for i in range(frames):
        im = Image.new("L", size, 255)
        d = ImageDraw.Draw(im)
        x = size[0] / 2 + size[0] / 4 * math.sin(i / 20)
        d.ellipse([x - 50, 30, x + 50, 140], fill=0)
        d.rectangle([x - 35, 110, x + 35, size[1]], fill=0)
        out.append(im.convert("P"))
    buf = io.BytesIO()
    out[0].save(buf, format="GIF", save_all=True, append_images=out[1:], duration=ms, loop=0)
    return buf.getvalue()


def _open(p: media.PreparedImage):
    im = Image.open(io.BytesIO(p.data))
    frames = list(ImageSequence.Iterator(im))
    return im, frames


def test_gif_is_display_sized_and_compact():
    p = media.prepare(_silhouette_gif(), 64, 16, budget_kb=150)
    im, frames = _open(p)
    assert p.ext == ".gif" and im.size == (64, 16)
    assert p.kb <= 150 and len(frames) == p.frames


def test_over_budget_drops_frames_but_keeps_speed():
    src = _silhouette_gif(frames=300, ms=40)
    full = media.prepare(src, 64, 64, budget_kb=10_000)
    small = media.prepare(src, 64, 64, budget_kb=40)
    assert small.kb <= 40 < full.kb
    assert small.frames < full.frames
    total = lambda p: sum(f.info["duration"] for f in _open(p)[1])
    assert abs(total(small) - total(full)) <= 40 * 4  # same overall length (rounding at the end)


def test_identical_frames_are_merged():
    frames = [Image.new("RGB", (8, 8), c) for c in ["red"] * 5 + ["blue"] * 5]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], duration=50, loop=0)
    p = media.prepare(buf.getvalue(), 8, 8)
    _, out = _open(p)
    assert p.frames == 2 and [f.info["duration"] for f in out] == [250, 250]


def test_too_short_delays_are_folded():
    frames = [Image.new("RGB", (8, 8), (i * 20, 0, 0)) for i in range(10)]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], duration=10, loop=0)
    _, out = _open(media.prepare(buf.getvalue(), 8, 8))
    assert all(f.info["duration"] >= media.MIN_FRAME_MS for f in out)


def test_static_image_becomes_png_with_black_transparency():
    im = Image.new("RGBA", (100, 50), (255, 0, 0, 0))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    p = media.prepare(buf.getvalue(), 32, 16)
    out = Image.open(io.BytesIO(p.data)).convert("RGB")
    assert p.ext == ".png" and out.size == (32, 16) and out.getpixel((5, 5)) == (0, 0, 0)
