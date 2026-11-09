"""Dutch public transport departures from OVapi (https://ovapi.nl – free, no key).

OVapi carries real-time data (KV78Turbo) for buses, trams, metro and ferries of
all Dutch operators. NS trains are not included. It is served over plain HTTP.

Stops are "stop areas" (a stop with all its platforms). The list of all stop
areas is downloaded once and cached for searching.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw

from .. import render
from .http import get_json

BASE = "http://v0.ovapi.nl"
TZ = ZoneInfo("Europe/Amsterdam")
STOP_LIST_MAX_AGE = 7 * 86400
DEPARTURES_CACHE = 30  # seconds; be gentle with a free service

TYPE_COLOURS = {  # (text, background) per transport type
    "BUS": ("ffffff", "1f5fbf"), "TRAM": ("ffffff", "c8102e"), "METRO": ("000000", "ff9a00"),
    "BOAT": ("ffffff", "0096a0"), "TRAIN": ("000000", "ffc917"),
}


@dataclass(frozen=True)
class Stop:
    code: str
    name: str
    town: str

    def label(self) -> str:
        return f"{self.name} ({self.town})" if self.town and self.town not in self.name else self.name


@dataclass(frozen=True)
class Departure:
    line: str
    destination: str
    expected: datetime
    planned: datetime
    transport: str = "BUS"
    cancelled: bool = False

    @property
    def delay_min(self) -> int:
        return round((self.expected - self.planned).total_seconds() / 60)

    def minutes(self, now: datetime) -> int:
        return max(0, int((self.expected - now).total_seconds() // 60))


# --- stops -------------------------------------------------------------------------

def _stop_cache() -> Path:
    from ..config import config_dir
    return config_dir() / "ovapi_stopareas.json"


def load_stops(force: bool = False) -> list[Stop]:
    """All Dutch stop areas (cached on disk for a week)."""
    path = _stop_cache()
    if not force and path.exists() and time.time() - path.stat().st_mtime < STOP_LIST_MAX_AGE:
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = get_json(f"{BASE}/stopareacode/", timeout=60)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    return parse_stops(data)


def parse_stops(data: dict) -> list[Stop]:
    out = []
    for code, s in (data or {}).items():
        if isinstance(s, dict):
            out.append(Stop(str(s.get("StopAreaCode") or code), s.get("TimingPointName", code),
                            s.get("TimingPointTown", "")))
    return out


def search_stops(stops: list[Stop], query: str, limit: int = 30) -> list[Stop]:
    words = query.lower().split()
    if not words:
        return []
    hits = [s for s in stops if all(w in f"{s.name} {s.town}".lower() for w in words)]
    hits.sort(key=lambda s: (not s.name.lower().startswith(words[0]), s.town, s.name))
    return hits[:limit]


# --- departures --------------------------------------------------------------------

def fetch_departures(stop_code: str, lines: list[str] | None = None, now: datetime | None = None) -> list[Departure]:
    data = get_json(f"{BASE}/stopareacode/{stop_code}", ttl=DEPARTURES_CACHE)
    return parse_departures(data, lines, now)


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=TZ)


def parse_departures(data: dict, lines: list[str] | None = None, now: datetime | None = None) -> list[Departure]:
    """Upcoming departures from an OVapi ``/stopareacode/<code>`` or ``/tpc/<code>`` reply."""
    now = now or datetime.now(TZ)
    wanted = {x.strip().upper() for x in lines or [] if x.strip()}
    out: dict[tuple, Departure] = {}
    for timing_points in (data or {}).values():
        if not isinstance(timing_points, dict):
            continue
        if "Passes" in timing_points:  # /tpc/<code> shape: one level less
            timing_points = {"tpc": timing_points}
        for tp in timing_points.values():
            for p in ((tp or {}).get("Passes") or {}).values():
                status = p.get("TripStopStatus", "")
                if status in ("PASSED", "ARRIVED") or not p.get("ExpectedDepartureTime"):
                    continue
                line = str(p.get("LinePublicNumber") or p.get("LineName") or "?")
                if wanted and line.upper() not in wanted:
                    continue
                dep = Departure(line, p.get("DestinationName50") or p.get("DestinationName16") or "",
                                _time(p["ExpectedDepartureTime"]),
                                _time(p.get("TargetDepartureTime") or p["ExpectedDepartureTime"]),
                                p.get("TransportType", "BUS"), status == "CANCEL")
                if dep.expected < now.replace(second=0, microsecond=0):
                    continue
                key = (dep.line, dep.destination, dep.planned)  # one trip seen at two platforms
                out.setdefault(key, dep)
    return sorted(out.values(), key=lambda d: d.expected)


# --- drawing -----------------------------------------------------------------------

def transport_icon(kind: str, size: int) -> Image.Image:
    """Small pictogram for the kind of vehicle, ``size`` pixels square."""
    s = size
    im = Image.new("RGB", (s, s))
    d = ImageDraw.Draw(im)
    fg = (230, 230, 230)
    win = (0, 0, 0)
    k = (kind or "BUS").upper()

    def body(top: float, bottom: float, radius: int):
        d.rounded_rectangle([s * 0.08, s * top, s * 0.92, s * bottom], radius=radius, fill=fg)

    if k == "METRO":
        d.ellipse([s * 0.05, s * 0.05, s * 0.95, s * 0.95], fill=(255, 154, 0))
        m = render.text_mask("M", 16)
        if m.width <= s and m.height <= s:
            im.paste((0, 0, 0), ((s - m.width) // 2, (s - m.height) // 2), m)
        return im
    if k == "BOAT":
        d.polygon([(s * 0.05, s * 0.55), (s * 0.95, s * 0.55), (s * 0.78, s * 0.85), (s * 0.22, s * 0.85)], fill=fg)
        d.rectangle([s * 0.3, s * 0.3, s * 0.65, s * 0.55], fill=fg)
        d.line([s * 0.05, s * 0.95, s * 0.95, s * 0.95], fill=(60, 140, 255), width=max(1, s // 16))
        return im
    if k == "TRAM":
        d.line([s * 0.5, s * 0.02, s * 0.5, s * 0.18], fill=fg, width=max(1, s // 16))   # pantograph
        d.line([s * 0.3, s * 0.02, s * 0.7, s * 0.02], fill=fg, width=max(1, s // 16))
        body(0.18, 0.85, max(1, s // 6))
    elif k == "TRAIN":
        body(0.12, 0.85, max(2, s // 3))
    else:  # BUS
        body(0.12, 0.8, max(1, s // 8))
    # windows and wheels
    d.rectangle([s * 0.2, s * 0.3, s * 0.8, s * 0.5], fill=win)
    for cx in (0.3, 0.7):
        d.ellipse([s * (cx - 0.1), s * 0.75, s * (cx + 0.1), s * 0.95], fill=(150, 150, 150))
    return im


def _short(text: str, n: int = 18) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def segments(deps: list[Departure], height: int, label: str = "", count: int = 3,
             now: datetime | None = None) -> list[Image.Image]:
    now = now or datetime.now(TZ)
    size = render.segment_font_size(height)
    segs: list[Image.Image] = []
    if label:
        segs.append(render.text_segment(label, "aaaaaa", height, size))
    if not deps:
        segs.append(render.text_segment("geen vertrektijden", "888888", height, size))
        return segs
    for d in deps[:count]:
        fg, bg = TYPE_COLOURS.get(d.transport, TYPE_COLOURS["BUS"])
        segs.append(transport_icon(d.transport, min(height, size)))
        segs.append(render.badge(d.line, fg, bg, height, size))
        segs.append(render.text_segment(_short(d.destination), "ffffff", height, size))
        if d.cancelled:
            segs.append(render.text_segment("vervalt", "ff3333", height, size))
            continue
        mins = d.minutes(now)
        segs.append(render.text_segment("nu" if mins == 0 else f"{mins}'", "55ff55" if mins <= 1 else "ffdd55",
                                        height, size))
        if d.delay_min >= 2:
            segs.append(render.text_segment(f"+{d.delay_min}", "ff8833", height, size))
    return segs


def frames(deps: list[Departure], width: int, height: int, label: str = "", count: int = 3,
           now: datetime | None = None, speed: float = render.SCROLL_SPEEDS["normal"]) -> list[render.Frame]:
    return render.marquee(segments(deps, height, label, count, now), width, height, gap=5, speed=speed)
