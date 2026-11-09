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
from pathlib import Path
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
    #: Live presets (weather, departures, schedules): re-check every this many
    #: seconds while shown and update the display when the content changed. 0 = static.
    refresh: float = 0

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
    """Build a preset from a saved ``{"name", "steps": [[command, params], ...]}`` entry.

    Image presets use an ``image_file`` step naming a copy kept in the media folder;
    the device manager prepares that file for whichever display it is sent to.
    """
    steps = [(str(c), dict(p)) for c, p in entry.get("steps", [])]
    image = None
    if steps and steps[0][0] == "image_file":
        image = _file_thumbnail(steps[0][1].get("file", ""))
    if steps and steps[0][0] in LIVE_KINDS:
        kind, params = steps[0]
        thumb = LIVE_KINDS[kind]
        if kind == "schedule" and params.get("icon"):
            thumb = _event_thumb(params["icon"], thumb)
        return Preset(entry["name"], "My presets", entry.get("description", "Live"),
                      lambda w, h: [live_step(kind, params, w, h)], tuple(entry.get("swatch", ())),
                      image=thumb, refresh=float(params.get("refresh") or LIVE_REFRESH[kind]))
    return Preset(entry["name"], "My presets", entry.get("description", "Saved preset"),
                  lambda _w, _h: [(c, dict(p)) for c, p in steps], tuple(entry.get("swatch", ())),
                  image=image)


def _file_thumbnail(name: str):
    def draw(w: int, h: int) -> Image.Image:
        from .config import media_dir
        from PIL import ImageOps
        try:
            with Image.open(media_dir() / Path(name).name) as im:
                return ImageOps.fit(im.convert("RGB"), (w, h))
        except OSError:
            return draw_stripes(w, h, ["444444"])
    return draw


def all_presets(saved: list[dict] | None = None) -> list[Preset]:
    return BUILTIN + [user_preset(e) for e in (saved or [])]


def find(name: str, saved: list[dict] | None = None) -> Preset | None:
    key = name.strip().lower()
    return next((p for p in all_presets(saved) if p.name.lower() == key), None)


# --- live presets (fetch fresh data every time they are shown) -------------------------

def _weather_thumb(w: int, h: int) -> Image.Image:
    from .live.weather import icon
    im = Image.new("RGB", (w, h))
    ic = icon(2, h)
    im.paste(ic, ((w - ic.width) // 2, 0))
    return im


def _transit_thumb(w: int, h: int) -> Image.Image:
    im = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(im)
    d.rectangle([w * 0.2, h * 0.1, w * 0.8, h * 0.75], fill=(255, 200, 0))
    d.rectangle([w * 0.27, h * 0.2, w * 0.73, h * 0.45], fill=(30, 30, 30))
    d.ellipse([w * 0.27, h * 0.75, w * 0.37, h * 0.95], fill=(200, 200, 200))
    d.ellipse([w * 0.63, h * 0.75, w * 0.73, h * 0.95], fill=(200, 200, 200))
    return im


def _schedule_thumb(w: int, h: int) -> Image.Image:
    im = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(im)
    d.rectangle([w * 0.2, h * 0.15, w * 0.8, h * 0.9], fill=(230, 230, 230))
    d.rectangle([w * 0.2, h * 0.15, w * 0.8, h * 0.35], fill=(255, 170, 0))
    for y in (0.5, 0.65, 0.8):
        d.line([w * 0.3, h * y, w * 0.7, h * y], fill=(60, 60, 60))
    return im


LIVE_KINDS = {"weather": _weather_thumb, "transit": _transit_thumb, "schedule": _schedule_thumb}
#: How often a shown live preset is re-checked by default (seconds). Departures
#: count down per minute; schedules change per talk; weather updates slowly.
LIVE_REFRESH = {"weather": 600, "transit": 60, "schedule": 120}


def _event_thumb(spec: str, fallback):
    """The event's own icon as the preset button picture (cached; no network if cached)."""
    def draw(w: int, h: int) -> Image.Image:
        from .live import events
        icon = events.icon(spec, fetch=False)  # thumbnails are drawn on the GUI thread: cache only
        if icon is None:
            return fallback(w, h)
        im = Image.new("RGB", (w, h))
        icon = icon.copy()
        icon.thumbnail((w, h))
        im.paste(icon, ((w - icon.width) // 2, (h - icon.height) // 2), icon)
        return im
    return draw


def _speed(params: dict):
    """Scroll speed name ("slow"/"normal"/"fast") or characters per second."""
    return params.get("speed") or "normal"


def live_step(kind: str, params: dict, width: int, height: int) -> Step:
    """Fetch and draw live data (runs in a worker thread; does network I/O)."""
    if kind == "weather":
        from .live import weather
        data = weather.fetch(float(params["latitude"]), float(params["longitude"]),
                             int(params.get("forecast_days", 2)))
        frames = weather.frames(data, width, height, params.get("label", ""),
                                int(params.get("forecast_days", 2)), params.get("lang", "nl"),
                                speed=_speed(params), layout=params.get("layout", "auto"))
    elif kind == "transit":
        from .live import transit
        if params.get("source") == "ns":
            from .live import ns
            deps = ns.fetch_departures(params["stop"], lines=params.get("lines") or None)
        else:
            deps = transit.fetch_departures(params["stop"], lines=params.get("lines") or None)
        frames = transit.frames(deps, width, height, params.get("label", ""),
                                int(params.get("count", 3)), speed=_speed(params),
                                layout=params.get("layout", "auto"))
    elif kind == "schedule":
        from .live import events, schedule
        sched = schedule.fetch(params["url"])
        frames = schedule.frames(sched, width, height, count=int(params.get("count", 4)),
                                 rooms=params.get("rooms") or None, icon=events.icon(params.get("icon", "")),
                                 label=params.get("label", ""), lang=params.get("lang", "en"),
                                 speed=_speed(params), layout=params.get("layout", "auto"))
    else:
        raise ValueError(f"unknown live preset {kind!r}")
    if len(frames) == 1:
        return _png_step(frames[0][0])
    # Long scrolls make many frames: use the compact encoder (shared palette) and
    # keep every frame so the scroll stays smooth and at the chosen speed.
    from . import media
    prepared = media.prepare_frames([f for f, _ in frames], [d for _, d in frames], budget_kb=10_000)
    return ("send_image_b64", {"data": base64.b64encode(prepared.data).decode(), "ext": ".gif",
                               "resize_method": "fit"})
