"""Draw text effects as animated GIFs at a display's exact resolution.

Effects drawn here (flashes, rainbow colouring) only depend on GIF playback,
which every display supports, rather than on undocumented firmware flags.
"""

from __future__ import annotations

import colorsys
import io
from functools import lru_cache
from dataclasses import dataclass, field
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
                hold_ms: int = 2500, scroll_ms: int = 60, scroll: bool | None = None) -> list[Frame]:
    """Text centred if it fits, otherwise one pass scrolling right-to-left.

    ``scroll`` forces scrolling (True) or a still frame (False; long text is
    then cut off) instead of deciding by width.

    ``color`` is a hex string, or a callable ``(frame_index) -> Image`` giving
    a full-size fill image (used for rainbow colouring).
    """
    mask, fits = fit_text(text, width, height)
    fill_for = color if callable(color) else (lambda _i, c=_rgb(color): c)
    if scroll is not None:
        fits = not scroll
    if fits:
        frame = Image.new("RGB", (width, height), _rgb(bg))
        _place(frame, mask, max(0, (width - mask.width) // 2), fill_for(0))
        return [(frame, hold_ms)]
    distance = width + mask.width
    step = max(1, -(-distance // MAX_SCROLL_FRAMES))
    frames = []
    for i, x in enumerate(range(width, -mask.width - 1, -step)):
        frame = Image.new("RGB", (width, height), _rgb(bg))
        _place(frame, mask, x, fill_for(i))
        frames.append((frame, scroll_ms))
    return frames


def blink(frames: list[Frame], on_ms: int = 500, off_ms: int = 400, bg: str = "000000") -> list[Frame]:
    """Alternate the (first) text frame with a blank one."""
    first = frames[0][0]
    return [(first, on_ms), (Image.new("RGB", first.size, _rgb(bg)), off_ms)]


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


# --- strips of text and icons (weather, departures) ---------------------------------

def segment_font_size(height: int, width: int | None = None) -> int:
    """Crisp Unifont size (16, 32, …) for this panel; see :func:`font_for`."""
    return font_for(width, height) if width else max(16, 16 * (height // 16))


def text_segment(text: str, color: str, height: int, size: int | None = None) -> Image.Image:
    """Text drawn on black, ``height`` tall, as narrow as the text."""
    mask = text_mask(text, size or segment_font_size(height))
    seg = Image.new("RGB", (mask.width, height))
    seg.paste(_rgb(color), (0, max(0, (height - mask.height) // 2)), mask)
    return seg


def badge(text: str, fg: str, bg: str, height: int, size: int | None = None, pad: int = 2) -> Image.Image:
    """Text on a coloured block (line numbers)."""
    mask = text_mask(text, size or segment_font_size(height))
    seg = Image.new("RGB", (mask.width + 2 * pad, height), _rgb(bg))
    seg.paste(_rgb(fg), (pad, max(0, (height - mask.height) // 2)), mask)
    return seg


#: Scroll speeds in characters per second, so text feels equally fast on any panel size.
SCROLL_SPEEDS = {"slow": 2.0, "normal": 3.0, "fast": 5.0}
MAX_MARQUEE_FRAMES = 600
ROW_HEIGHT = 16


def font_for(width: int, height: int) -> int:
    """Font size for one line of text: as tall as the panel allows, but small
    enough that at least ~8 characters fit across (Unifont glyphs are half as
    wide as they are tall)."""
    by_height = max(1, height // 16)
    by_width = max(1, width // 64)
    return 16 * min(by_height, by_width)


def pixels_per_second(speed, size: int) -> float:
    """``speed`` is a name from SCROLL_SPEEDS or characters per second."""
    cps = SCROLL_SPEEDS.get(speed, speed) if isinstance(speed, str) else speed
    try:
        cps = float(cps)
    except (TypeError, ValueError):
        cps = SCROLL_SPEEDS["normal"]
    return max(1.0, cps * size / 2)


def _strip(segments: list[Image.Image], height: int, gap: int) -> Image.Image:
    total = sum(s.width for s in segments) + gap * max(0, len(segments) - 1)
    strip = Image.new("RGB", (max(1, total), height))
    x = 0
    for s in segments:
        strip.paste(s, (x, (height - s.height) // 2))
        x += s.width + gap
    return strip


def _step_and_ms(distance: int, pps: float) -> tuple[int, int]:
    step = max(1, -(-distance // MAX_MARQUEE_FRAMES))
    if step == 1 and pps > 30:
        step = 2  # fewer frames for the same speed
    return step, max(20, round(step * 1000 / pps))


def marquee(segments: list[Image.Image], width: int, height: int, gap: int = 4, hold_ms: int = 4000,
            speed="normal", size: int = 16, lead_ms: int = 1200) -> list[Frame]:
    """Join segments left to right; show still if it fits, otherwise scroll it.

    The text starts already on screen at the left edge and pauses ``lead_ms``
    so its beginning can be read, then scrolls until it has left the panel –
    no seconds of empty panel while text crawls in from the right (which
    matters most on small 64×16 panels).
    """
    strip = _strip(segments, height, gap)
    total = strip.width
    if total <= width:
        frame = Image.new("RGB", (width, height))
        frame.paste(strip, ((width - total) // 2, 0))
        return [(frame, hold_ms)]
    step, frame_ms = _step_and_ms(total, pixels_per_second(speed, size))
    out = []
    for x in range(0, -total - 1, -step):
        frame = Image.new("RGB", (width, height))
        frame.paste(strip, (x, 0))
        out.append((frame, frame_ms))
    out[0] = (out[0][0], max(frame_ms, lead_ms))
    return out


@dataclass
class Row:
    """One line of a multi-line layout: ``left`` and ``right`` stay put, ``body``
    sits between them and scrolls if it doesn't fit."""
    left: list = field(default_factory=list)
    body: list = field(default_factory=list)
    right: list = field(default_factory=list)


def rows_frames(rows: list[Row], width: int, height: int, speed="normal", gap: int = 3,
                hold_ms: int = 4000, pause_ms: int = 1200) -> list[Frame]:
    """Several 16-pixel rows stacked; rows whose middle part is too long scroll
    it (left to right edge), all at the same speed. Rows that fit stay still."""
    rows = rows[: max(1, height // ROW_HEIGHT)]
    y0 = (height - len(rows) * ROW_HEIGHT) // 2
    pps = pixels_per_second(speed, ROW_HEIGHT)
    prepared = []
    longest = 0
    for r in rows:
        left = _strip(r.left, ROW_HEIGHT, gap) if r.left else None
        right = _strip(r.right, ROW_HEIGHT, gap) if r.right else None
        x0 = left.width + gap if left else 0
        x1 = width - (right.width + gap if right else 0)
        body = _strip(r.body, ROW_HEIGHT, gap) if r.body else None
        room = max(0, x1 - x0)
        travel = max(0, body.width - room) if body else 0
        longest = max(longest, travel)
        prepared.append((left, body, right, x0, x1, room, travel))

    def draw(offset: int) -> Image.Image:
        frame = Image.new("RGB", (width, height))
        for i, (left, body, right, x0, x1, room, travel) in enumerate(prepared):
            y = y0 + i * ROW_HEIGHT
            if body is not None and room > 0:
                o = min(offset, travel)
                frame.paste(body.crop((o, 0, o + room, ROW_HEIGHT)), (x0, y))
            if left is not None:
                frame.paste(left, (0, y))
            if right is not None:
                frame.paste(right, (width - right.width, y))
        return frame

    if longest == 0:
        return [(draw(0), hold_ms)]
    step, frame_ms = _step_and_ms(longest, pps)
    offsets = list(range(0, longest + 1, step))
    if offsets[-1] != longest:
        offsets.append(longest)
    frames = [(draw(o), frame_ms) for o in offsets]
    first, last = frames[0][0], frames[-1][0]
    return [(first, pause_ms)] + frames[1:-1] + [(last, pause_ms)]


def pages_frames(pages: list[list[Row]], width: int, height: int, speed="normal",
                 hold_ms: int = 3500) -> list[Frame]:
    """Show pages of rows one after another (like a station board paging)."""
    out: list[Frame] = []
    for page in pages:
        frames = rows_frames(page, width, height, speed, hold_ms=hold_ms)
        out.extend(frames)
    return out


def layout(one_line: list[Image.Image], items: list[list[Row]], width: int, height: int,
           mode: str = "auto", speed="normal", size: int = 16) -> list[Frame]:
    """One scrolling line, or rows: each item is one or more rows; items are
    grouped into pages that fit the panel's height. ``auto`` uses rows when the
    panel is at least two rows tall."""
    rows_fit = height // ROW_HEIGHT
    use_rows = mode == "rows" or (mode == "auto" and rows_fit >= 2)
    if use_rows and items and rows_fit >= 1:
        per_item = max(len(i) for i in items)
        per_page = max(1, rows_fit // per_item)
        pages = [[r for item in items[i:i + per_page] for r in item] for i in range(0, len(items), per_page)]
        return pages_frames(pages, width, height, speed)
    return marquee(one_line, width, height, speed=speed, size=size)
