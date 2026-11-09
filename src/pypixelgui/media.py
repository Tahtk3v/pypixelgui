"""Prepare images/GIFs for a display before they go over Bluetooth.

pypixelcolor re-encodes every GIF frame with its own 256-colour palette, which
can turn a black-and-white animation into megabytes; uploads that size take
minutes and the displays tend to drop the link part-way. Here we:

* resize every frame to the display's exact size,
* share one small palette across all frames (a silhouette needs 2 colours),
* merge consecutive identical frames (adding up their durations),
* and, if still over the size budget, drop frames evenly – stretching the
  remaining frames' durations so the animation keeps its speed.

The result is ready to send as-is (no further resizing needed).
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, ImageSequence

DEFAULT_BUDGET_KB = 150
#: Many GIF decoders treat delays below 20 ms as "slow" (100 ms) – avoid them.
MIN_FRAME_MS = 20
MAX_COLOURS = 16


@dataclass
class PreparedImage:
    data: bytes
    ext: str                 # ".gif" or ".png"
    width: int
    height: int
    source_frames: int = 1
    frames: int = 1
    source_bytes: int = 0

    @property
    def kb(self) -> float:
        return len(self.data) / 1024

    def describe(self) -> str:
        if self.ext == ".png":
            return f"image {self.width}×{self.height}, {self.kb:.1f} KB"
        kept = (f"{self.frames} of {self.source_frames} frames" if self.frames != self.source_frames
                else f"{self.frames} frames")
        return f"GIF {self.width}×{self.height}, {kept}, {self.kb:.1f} KB"


def _flatten(frame: Image.Image) -> Image.Image:
    """Transparent pixels become black (the LEDs are off there)."""
    rgba = frame.convert("RGBA")
    bg = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
    return Image.alpha_composite(bg, rgba).convert("RGB")


def _fit(img: Image.Image, width: int, height: int, method: str) -> Image.Image:
    if img.size == (width, height):
        return img
    if method == "fit":
        return ImageOps.pad(img, (width, height), method=Image.LANCZOS, color=(0, 0, 0))
    return ImageOps.fit(img, (width, height), method=Image.LANCZOS)


def _encode_gif(frames: list[Image.Image], durations: list[int], loop: int) -> bytes:
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], loop=loop,
                   duration=durations if len(frames) > 1 else durations[0],
                   disposal=1, optimize=False)
    return buf.getvalue()


def _thin(frames: list[Image.Image], durations: list[int], keep_every: int):
    out_f, out_d = [], []
    for i in range(0, len(frames), keep_every):
        out_f.append(frames[i])
        out_d.append(sum(durations[i:i + keep_every]))
    return out_f, out_d


def prepare(source: str | Path | bytes, width: int, height: int, method: str = "crop",
            budget_kb: float = DEFAULT_BUDGET_KB) -> PreparedImage:
    raw = source if isinstance(source, bytes) else Path(source).read_bytes()
    img = Image.open(io.BytesIO(raw))
    n_frames = getattr(img, "n_frames", 1)

    if n_frames <= 1:
        frame = _fit(_flatten(ImageOps.exif_transpose(img)), width, height, method)
        buf = io.BytesIO()
        frame.save(buf, format="PNG", optimize=True)
        return PreparedImage(buf.getvalue(), ".png", width, height, 1, 1, len(raw))

    loop = img.info.get("loop", 0)
    frames: list[Image.Image] = []
    durations: list[int] = []
    for f in ImageSequence.Iterator(img):
        frames.append(_fit(_flatten(f), width, height, method))
        durations.append(int(f.info.get("duration", img.info.get("duration", 100)) or 100))
    return prepare_frames(frames, durations, budget_kb, loop=loop, source_frames=n_frames, source_bytes=len(raw))


def prepare_frames(frames: list[Image.Image], durations: list[int], budget_kb: float = DEFAULT_BUDGET_KB,
                   *, loop: int = 0, source_frames: int | None = None, source_bytes: int = 0) -> PreparedImage:
    """Encode display-sized RGB frames compactly (see module docstring)."""
    width, height = frames[0].size
    n_frames = source_frames or len(frames)
    if len(frames) == 1:
        buf = io.BytesIO()
        frames[0].convert("RGB").save(buf, format="PNG", optimize=True)
        return PreparedImage(buf.getvalue(), ".png", width, height, n_frames, 1, source_bytes)

    # One palette for every frame, built from a sample of frames.
    sample = frames[:: max(1, len(frames) // 32)][:32]
    strip = Image.new("RGB", (width, height * len(sample)))
    for i, f in enumerate(sample):
        strip.paste(f.convert("RGB"), (0, i * height))
    palette = strip.quantize(colors=MAX_COLOURS, method=Image.Quantize.MEDIANCUT)
    pframes = [f.convert("RGB").quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]

    # Merge runs of identical frames.
    merged_f, merged_d = [pframes[0]], [durations[0]]
    for f, d in zip(pframes[1:], durations[1:]):
        if f.tobytes() == merged_f[-1].tobytes():
            merged_d[-1] += d
        else:
            merged_f.append(f)
            merged_d.append(d)

    # Too-short delays: fold frames together until each lasts long enough.
    keep = 1
    while keep < len(merged_f) and min(_thin(merged_f, merged_d, keep)[1]) < MIN_FRAME_MS:
        keep += 1
    out_f, out_d = _thin(merged_f, merged_d, keep)
    data = _encode_gif(out_f, out_d, loop)

    # Over budget: keep every 2nd, 3rd, … frame until it fits.
    while len(data) > budget_kb * 1024 and len(out_f) > 1:
        keep += 1
        out_f, out_d = _thin(merged_f, merged_d, keep)
        data = _encode_gif(out_f, out_d, loop)

    if len(out_f) == 1:  # everything merged into one still frame
        return prepare_frames([out_f[0].convert("RGB")], out_d[:1], budget_kb, source_frames=n_frames,
                              source_bytes=source_bytes)
    return PreparedImage(data, ".gif", width, height, n_frames, len(out_f), source_bytes)


def decode(prepared: PreparedImage) -> tuple[list[Image.Image], list[int]]:
    """RGB frames and durations of a prepared image."""
    im = Image.open(io.BytesIO(prepared.data))
    frames, durations = [], []
    for f in ImageSequence.Iterator(im):
        frames.append(f.convert("RGB"))
        durations.append(int(f.info.get("duration", 100) or 100))
    return frames, durations


def split(prepared: PreparedImage, cols: int, rows: int,
          budget_kb: float = DEFAULT_BUDGET_KB) -> list[PreparedImage]:
    """Cut an image for a ``cols``×``rows`` wall of identical panels into tiles (row-major).

    Every tile keeps the same frame timing, so panels that start together stay together.
    """
    frames, durations = decode(prepared)
    tw, th = prepared.width // cols, prepared.height // rows
    tiles = []
    for r in range(rows):
        for c in range(cols):
            box = (c * tw, r * th, (c + 1) * tw, (r + 1) * th)
            crops = [f.crop(box) for f in frames]
            if len(crops) == 1:
                tiles.append(prepare_frames(crops, durations[:1], budget_kb))
            else:
                # Keep every frame (even blank/duplicate ones) so tiles stay in step.
                tiles.append(_encode_aligned(crops, durations))
    return tiles


def _encode_aligned(frames: list[Image.Image], durations: list[int]) -> PreparedImage:
    width, height = frames[0].size
    sample = frames[:: max(1, len(frames) // 32)][:32]
    strip = Image.new("RGB", (width, height * len(sample)))
    for i, f in enumerate(sample):
        strip.paste(f, (0, i * height))
    palette = strip.quantize(colors=MAX_COLOURS, method=Image.Quantize.MEDIANCUT)
    pframes = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    # Pillow merges identical consecutive frames; a 1-pixel marker difference
    # would show, so instead keep their combined duration – timing is preserved.
    data = _encode_gif(pframes, durations, 0)
    return PreparedImage(data, ".gif", width, height, len(frames), len(frames), len(data))
