"""Ready-made looks for a display.

A preset turns into a list of ``(command, params)`` steps for the connected
display's size, using only commands from :mod:`pypixelgui.commands`, so the
same validation applies whether a preset is triggered from the GUI or remotely.

Flags are drawn with Pillow at the display's exact resolution, so stripes stay
crisp even on 32×16 panels.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field
from typing import Callable

from PIL import Image, ImageDraw

from . import render

Step = tuple[str, dict]


@dataclass(frozen=True)
class Preset:
    name: str
    category: str
    description: str
    build: Callable[[int, int], list[Step]] = field(repr=False)
    #: Colours for the GUI thumbnail when there's no ``image``.
    swatch: tuple[str, ...] = ()
    #: Draws the preset's picture (flags) – used for the GUI thumbnail.
    image: Callable[[int, int], Image.Image] | None = field(default=None, repr=False)

    def steps(self, width: int, height: int) -> list[Step]:
        return self.build(width, height)

    def thumbnail(self, width: int = 48, height: int = 28) -> Image.Image:
        if self.image is not None:
            return self.image(width, height)
        return draw_stripes(width, height, list(self.swatch) or ["444444"])


# --- text presets ------------------------------------------------------------

def _text(text: str, color: str, animation: str, speed: int = 80, bg: str | None = None,
          rainbow: int = 0) -> Callable[[int, int], list[Step]]:
    def build(_w: int, _h: int) -> list[Step]:
        p = {"text": text, "color": color, "animation": animation, "speed": speed, "rainbow_mode": rainbow}
        if bg:
            p["bg_color"] = bg
        return [("send_text", p)]
    return build


def _gif_step(frames: list) -> Step:
    data = render.encode_gif(frames)
    return ("send_image_b64", {"data": base64.b64encode(data).decode(), "ext": ".gif",
                               "resize_method": "fit"})


def _flash_then_text(text: str, color: str, flash_color: str | None = None,
                     bg: str = "000000") -> Callable[[int, int], list[Step]]:
    """Whole panel flashes, then the text shows; the GIF loops on the display."""
    def build(w: int, h: int) -> list[Step]:
        frames = render.flash_frames(w, h, flash_color or color, off_color=bg)
        frames += render.text_frames(text, w, h, color, bg=bg)
        return [_gif_step(frames)]
    return build


def _rainbow_text(text: str) -> Callable[[int, int], list[Step]]:
    return lambda w, h: [_gif_step(render.rainbow_text_frames(text, w, h))]


# --- flags -------------------------------------------------------------------

def _png_step(img: Image.Image) -> Step:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return ("send_image_b64", {"data": base64.b64encode(buf.getvalue()).decode(), "ext": ".png",
                               "resize_method": "fit"})


def _rgb(h: str) -> tuple[int, int, int]:
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def draw_stripes(width: int, height: int, colors: list[str], weights: list[float] | None = None) -> Image.Image:
    """Horizontal stripes; ``weights`` gives relative heights (default equal)."""
    weights = weights or [1.0] * len(colors)
    total = sum(weights)
    img = Image.new("RGB", (width, height))
    d = ImageDraw.Draw(img)
    acc = 0.0
    for color, w in zip(colors, weights):
        top = round(acc / total * height)
        acc += w
        bottom = round(acc / total * height)
        if bottom > top:
            d.rectangle([0, top, width - 1, bottom - 1], fill=_rgb(color))
    return img


RAINBOW = ["e40303", "ff8c00", "ffed00", "008026", "004dff", "750787"]


def draw_progress(width: int, height: int) -> Image.Image:
    """Rainbow with the Progress chevron (parallel 45° bands from the hoist)."""
    img = draw_stripes(width, height, RAINBOW)
    d = ImageDraw.Draw(img)
    bands = ["000000", "613915", "5bcefa", "f5a9b8", "ffffff"]  # outermost first
    cy = (height - 1) / 2
    step = max(1.0, height / 10)
    tip = step * (len(bands) + 1) + cy * 0.3
    for i, color in enumerate(bands):
        x = tip - i * step
        d.polygon([(x - cy - 0.5, -1), (x, cy), (x - cy - 0.5, height),
                   (-width, height), (-width, -1)], fill=_rgb(color))
    return img


def draw_intersex(width: int, height: int) -> Image.Image:
    img = Image.new("RGB", (width, height), _rgb("ffd800"))
    d = ImageDraw.Draw(img)
    r = max(2.0, min(width, height) * 0.3)
    t = max(1, round(r * 0.3))
    cx, cy = (width - 1) / 2, (height - 1) / 2
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=_rgb("7902aa"), width=t)
    return img


_FLAGS: dict[str, tuple[Callable[[int, int], Image.Image], tuple[str, ...]]] = {
    "Rainbow": (lambda w, h: draw_stripes(w, h, RAINBOW), tuple(RAINBOW)),
    "Progress": (draw_progress, ("5bcefa", "f5a9b8", "ffffff", "e40303", "004dff")),
    "Trans": (lambda w, h: draw_stripes(w, h, ["5bcefa", "f5a9b8", "ffffff", "f5a9b8", "5bcefa"]),
              ("5bcefa", "f5a9b8", "ffffff")),
    "Bisexual": (lambda w, h: draw_stripes(w, h, ["d60270", "9b4f96", "0038a8"], [2, 1, 2]),
                 ("d60270", "9b4f96", "0038a8")),
    "Pansexual": (lambda w, h: draw_stripes(w, h, ["ff218c", "ffd800", "21b1ff"]),
                  ("ff218c", "ffd800", "21b1ff")),
    "Lesbian": (lambda w, h: draw_stripes(w, h, ["d52d00", "ff9a56", "ffffff", "d362a4", "a30262"]),
                ("d52d00", "ff9a56", "ffffff", "d362a4", "a30262")),
    "Non-binary": (lambda w, h: draw_stripes(w, h, ["fcf434", "ffffff", "9c59d1", "2c2c2c"]),
                   ("fcf434", "ffffff", "9c59d1", "2c2c2c")),
    "Asexual": (lambda w, h: draw_stripes(w, h, ["000000", "a3a3a3", "ffffff", "800080"]),
                ("000000", "a3a3a3", "ffffff", "800080")),
    "Intersex": (draw_intersex, ("ffd800", "7902aa")),
}


def _flag(draw: Callable[[int, int], Image.Image]) -> Callable[[int, int], list[Step]]:
    return lambda w, h: [_png_step(draw(w, h))]


BUILTIN: list[Preset] = [
    Preset("CYBER", "Text", "All LEDs flash yellow, then “CYBER” in yellow (loops)",
           _flash_then_text("CYBER", "ffff00"), ("ffff00", "000000")),
    Preset("ALERT", "Text", "Flashing red “ALERT”", _text("ALERT", "ff0000", "blink", speed=95),
           ("ff0000",)),
    Preset("ON AIR", "Text", "Steady red “ON AIR”", _text("ON AIR", "ff2020", "static"), ("ff2020",)),
    Preset("Do not disturb", "Text", "Scrolling amber “DO NOT DISTURB”",
           _text("DO NOT DISTURB", "ffaa00", "scroll_left", speed=70), ("ffaa00",)),
    Preset("Rainbow welcome", "Text", "“WELCOME” in flowing rainbow colours",
           _rainbow_text("WELCOME"), tuple(RAINBOW)),
] + [
    Preset(f"{name} flag", "Pride", f"{name} pride flag", _flag(draw), swatch, image=draw)
    for name, (draw, swatch) in _FLAGS.items()
]


def user_preset(entry: dict) -> Preset:
    """Build a preset from a saved ``{"name", "steps": [[command, params], ...]}`` entry."""
    steps = [(str(c), dict(p)) for c, p in entry.get("steps", [])]
    return Preset(entry["name"], "My presets", entry.get("description", "Saved preset"),
                  lambda _w, _h: [(c, dict(p)) for c, p in steps], tuple(entry.get("swatch", ())))


def all_presets(saved: list[dict] | None = None) -> list[Preset]:
    return BUILTIN + [user_preset(e) for e in (saved or [])]


def find(name: str, saved: list[dict] | None = None) -> Preset | None:
    key = name.strip().lower()
    return next((p for p in all_presets(saved) if p.name.lower() == key), None)
