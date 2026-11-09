"""Event schedules in the frab XML format (frab, pretalx, BornHack, CCC, eth0, …).

Shows what's on now and what's next, with the room (where at the event it
happens) and the event's own icon.

Times: frab exports give each talk a ``<date>`` and a local ``<start>``. Some
generators stamp local times with "+00:00" (BornHack), so the clock time is
taken from ``<start>`` and the timezone from the conference/day instead.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from PIL import Image

from .. import render
from .http import get_bytes

CACHE_SECONDS = 300
DAYS = {"en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], "nl": ["ma", "di", "wo", "do", "vr", "za", "zo"]}
WORDS = {"en": {"now": "now", "done": "event finished", "none": "nothing scheduled"},
         "nl": {"now": "nu", "done": "evenement voorbij", "none": "niets gepland"}}


@dataclass(frozen=True)
class Talk:
    start: datetime
    end: datetime
    title: str
    room: str
    persons: tuple[str, ...] = ()
    kind: str = ""


@dataclass
class Schedule:
    title: str
    talks: list[Talk] = field(default_factory=list)
    start: date | None = None
    end: date | None = None

    @property
    def rooms(self) -> list[str]:
        return sorted({t.room for t in self.talks if t.room})


def _duration(text: str) -> timedelta:
    parts = [int(p) for p in (text or "0:0").strip().split(":") if p.strip().isdigit()]
    while len(parts) < 3:
        parts.append(0)
    h, m, s = parts[:3]
    return timedelta(hours=h, minutes=m, seconds=s)


def _tz_from(value: str | None, fallback) -> object:
    if not value:
        return fallback
    try:
        return datetime.fromisoformat(value).tzinfo or fallback
    except ValueError:
        return fallback


def parse_frab_xml(data: bytes) -> Schedule:
    root = ET.fromstring(data)
    conf = root.find("conference")
    title = (conf.findtext("title") if conf is not None else "") or ""
    tz_name = conf.findtext("time_zone_name") if conf is not None else None
    try:
        conf_tz = ZoneInfo(tz_name) if tz_name else None
    except Exception:
        conf_tz = None
    sched = Schedule(title.strip())
    try:
        sched.start = date.fromisoformat(conf.findtext("start")[:10])
        sched.end = date.fromisoformat(conf.findtext("end")[:10])
    except (AttributeError, TypeError, ValueError):
        pass
    for day in root.findall("day"):
        day_tz = conf_tz or _tz_from(day.get("start"), timezone.utc)
        for ev in day.iter("event"):
            start_text = (ev.findtext("start") or "00:00").strip()
            hh, mm = (int(x) for x in start_text.split(":")[:2])
            date_text = (ev.findtext("date") or day.get("date") or "")[:10]
            try:
                local = datetime.combine(date.fromisoformat(date_text), time(hh, mm), tzinfo=day_tz)
            except ValueError:
                continue
            persons = tuple(p.text.strip() for p in ev.iter("person") if p.text and p.text.strip())
            sched.talks.append(Talk(local, local + _duration(ev.findtext("duration") or "0:00"),
                                    (ev.findtext("title") or "").strip(), (ev.findtext("room") or "").strip(),
                                    persons, (ev.findtext("type") or "").strip()))
    sched.talks.sort(key=lambda t: (t.start, t.room))
    return sched


def fetch(url: str) -> Schedule:
    return parse_frab_xml(get_bytes(url, ttl=CACHE_SECONDS, timeout=30))


def current_and_next(sched: Schedule, now: datetime, count: int = 4,
                     rooms: list[str] | None = None) -> list[tuple[Talk, bool]]:
    """Talks running now (flag True) followed by the next ones, at most ``count``."""
    wanted = {r.lower() for r in rooms or [] if r}
    talks = [t for t in sched.talks if not wanted or t.room.lower() in wanted]
    running = [(t, True) for t in talks if t.start <= now < t.end]
    upcoming = [(t, False) for t in talks if t.start > now]
    return (running + upcoming)[:count]


# --- drawing ----------------------------------------------------------------------

def _when(talk: Talk, running: bool, now: datetime, lang: str) -> str:
    if running:
        return WORDS.get(lang, WORDS["en"])["now"]
    local = talk.start.astimezone(now.tzinfo) if now.tzinfo else talk.start
    if local.date() == now.date():
        return local.strftime("%H:%M")
    return f"{DAYS.get(lang, DAYS['en'])[local.weekday()]} {local.strftime('%H:%M')}"


def _status(sched: Schedule, now: datetime, lang: str) -> str:
    words = WORDS.get(lang, WORDS["en"])
    if sched.talks and now > max(t.end for t in sched.talks):
        return words["done"]
    return words["none"]


def segments(sched: Schedule, entries, now: datetime, height: int, size: int, icon: Image.Image | None,
             label: str, lang: str) -> list[Image.Image]:
    segs: list[Image.Image] = []
    if icon is not None:
        segs.append(_fit_icon(icon, min(height, size)))
    segs.append(render.text_segment(label or sched.title, "ffaa00", height, size))
    if not entries:
        segs.append(render.text_segment(_status(sched, now, lang), "888888", height, size))
    for talk, running in entries:
        segs.append(render.text_segment(_when(talk, running, now, lang), "55ff55" if running else "ffdd55",
                                        height, size))
        segs.append(render.text_segment(talk.title, "ffffff", height, size))
        if talk.room:
            segs.append(render.text_segment(f"@ {talk.room}", "66ccff", height, size))
    return segs


def items(sched: Schedule, entries, now: datetime, width: int, icon: Image.Image | None, label: str,
          lang: str, height: int = 64) -> list[list[render.Row]]:
    """Rows per talk. Tall panels (4+ rows) start with an [icon] title header; on
    shorter ones the icon goes in front of every talk instead, so no page is wasted."""
    h = render.ROW_HEIGHT
    small_icon = [_fit_icon(icon, h)] if icon is not None else []
    with_header = height // h >= 4
    out: list[list[render.Row]] = []
    if with_header or not entries:
        out.append([render.Row(left=small_icon, body=[render.text_segment(label or sched.title, "ffaa00", h)])])
    if not entries:
        out[0].append(render.Row(body=[render.text_segment(_status(sched, now, lang), "888888", h)]))
        return out
    lead = [] if with_header else small_icon
    for talk, running in entries:
        when = render.text_segment(_when(talk, running, now, lang), "55ff55" if running else "ffdd55", h)
        room = [render.text_segment(talk.room, "66ccff", h)] if talk.room else []
        title = [render.text_segment(talk.title, "ffffff", h)]
        if width >= 128:
            out.append([render.Row(left=lead + [when], body=title + room)])
        else:
            out.append([render.Row(left=lead + [when], body=room), render.Row(body=title)])
    return out


def _fit_icon(icon: Image.Image, size: int) -> Image.Image:
    im = icon.convert("RGBA")
    im.thumbnail((size, size), Image.LANCZOS)
    out = Image.new("RGB", (size, size))
    out.paste(im, ((size - im.width) // 2, (size - im.height) // 2), im)
    return out


def frames(sched: Schedule, width: int, height: int, now: datetime | None = None, count: int = 4,
           rooms: list[str] | None = None, icon: Image.Image | None = None, label: str = "",
           lang: str = "en", speed="normal", layout: str = "auto") -> list[render.Frame]:
    now = now or datetime.now(ZoneInfo("Europe/Amsterdam"))
    entries = current_and_next(sched, now, count, rooms)
    size = render.font_for(width, height)
    return render.layout(segments(sched, entries, now, height, size, icon, label, lang),
                         items(sched, entries, now, width, icon, label, lang, height), width, height, layout,
                         speed, size)
