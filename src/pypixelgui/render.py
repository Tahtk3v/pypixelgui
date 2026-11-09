"""Draw text effects as animated GIFs at a display's exact resolution.

Effects drawn here (flashes, rainbow colouring) only depend on GIF playback,
which every display supports, rather than on undocumented firmware flags.
"""

from __future__ import annotations

import colorsys
import io
from functools import lru_cache
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont

Frame = tuple[Image.Image, int]  # (image, duration in ms)

#: Upper bound on frames for scrolling text, to keep uploads over BLE quick.
MAX_SCROLL_FRAMES = 64


def _rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


@lru_cache(maxsize=None)
def _font(size: int) -> ImageFont.FreeTypeFont:
    try:
        from pypixelcolor.lib.font_config import UNIFONT_PATH
        return ImageFont.truetype(UNIFONT_PATH, size)
    except Exception:  # pragma: no cover - fallback if pypixelcolor moves its font
        return ImageFont.load_default(size)


def text_mask(text: str, size: int) -> Image.Image:
    """1-bit-looking mask (L mode, 0/255) of ``text`` cropped to its ink."""
    font = _font(size)
    left, top, right, bottom = font.getbbox(text)
    im = Image.new("L", (max(1, right - left), max(1, bottom - top)))
    ImageDraw.Draw(im).text((-left, -top), text, font=font, fill=255)
    return im.point(lambda v: 255 if v >= 128 else 0)


def fit_text(text: str, width: int, height: int) -> tuple[Image.Image, bool]:
    """Pick the largest crisp font size (Unifont is a 16 px bitmap font, so
    multiples of 16; smaller sizes turn into unreadable dots).

    Returns the mask and whether it fits the width without scrolling.
    """
    sizes = [16 * k for k in range(max(1, height // 16), 0, -1)]
    best_tall = None
    for size in sizes:
        mask = text_mask(text, size)
        if mask.height > height:
            continue
        if mask.width <= width:
            return mask, True
        best_tall = best_tall or mask
    return (best_tall or text_mask(text, sizes[-1])), False


def _place(canvas: Image.Image, mask: Image.Image, x: int, fill) -> None:
    y = (canvas.height - mask.height) // 2
    if isinstance(fill, Image.Image):
        canvas.paste(fill, (0, 0), _shifted(mask, canvas.size, x, y))
    else:
        canvas.paste(fill, (x, y), mask)


def _shifted(mask: Image.Image, size: tuple[int, int], x: int, y: int) -> Image.Image:
    full = Image.new("L", size)
    full.paste(mask, (x, y))
    return full


def text_frames(text: str, width: int, height: int, color, bg: str = "000000",
                hold_ms: int = 2500, scroll_ms: int = 60) -> list[Frame]:
    """Text centred if it fits, otherwise one pass scrolling right-to-left.

    ``color`` is a hex string, or a callable ``(frame_index) -> Image`` giving
    a full-size fill image (used for rainbow colouring).
    """
    mask, fits = fit_text(text, width, height)
    fill_for = color if callable(color) else (lambda _i, c=_rgb(color): c)
    if fits:
        frame = Image.new("RGB", (width, height), _rgb(bg))
        _place(frame, mask, (width - mask.width) // 2, fill_for(0))
        return [(frame, hold_ms)]
    distance = width + mask.width
    step = max(1, -(-distance // MAX_SCROLL_FRAMES))
    frames = []
    for i, x in enumerate(range(width, -mask.width - 1, -step)):
        frame = Image.new("RGB", (width, height), _rgb(bg))
        _place(frame, mask, x, fill_for(i))
        frames.append((frame, scroll_ms))
    return frames


def flash_frames(width: int, height: int, color: str, times: int = 3, on_ms: int = 150,
                 off_ms: int = 120, off_color: str = "000000") -> list[Frame]:
    on = Image.new("RGB", (width, height), _rgb(color))
    off = Image.new("RGB", (width, height), _rgb(off_color))
    frames: list[Frame] = []
    for _ in range(times):
        frames += [(on, on_ms), (off, off_ms)]
    return frames


def rainbow_fill(width: int, height: int, frames: int = 24, spread: float = 1.0):
    """Fill factory: a horizontal rainbow that shifts a little every frame."""
    def fill(i: int) -> Image.Image:
        row = Image.new("RGB", (width, 1))
        for x in range(width):
            hue = (x / max(1, width) * spread + i / frames) % 1.0
            r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
            row.putpixel((x, 0), (int(r * 255), int(g * 255), int(b * 255)))
        return row.resize((width, height), Image.NEAREST)
    return fill


def rainbow_text_frames(text: str, width: int, height: int, cycle_frames: int = 24,
                        cycle_ms: int = 80) -> list[Frame]:
    fill = rainbow_fill(width, height, cycle_frames)
    mask, fits = fit_text(text, width, height)
    if not fits:
        return text_frames(text, width, height, fill)
    # Fits: keep it still and let the colours flow through it.
    out = []
    for i in range(cycle_frames):
        frame = Image.new("RGB", (width, height))
        _place(frame, mask, (width - mask.width) // 2, fill(i))
        out.append((frame, cycle_ms))
    return out


def encode_gif(frames: Iterable[Frame]) -> bytes:
    frames = list(frames)
    if not frames:
        raise ValueError("no frames")
    images = [f.convert("RGB") for f, _ in frames]
    buf = io.BytesIO()
    images[0].save(buf, format="GIF", save_all=True, append_images=images[1:],
                   duration=[d for _, d in frames], loop=0, disposal=1, optimize=False)
    return buf.getvalue()
