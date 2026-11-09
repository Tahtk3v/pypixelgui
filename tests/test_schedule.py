"""Event schedules: real frab exports from eth0 and BornHack (trimmed), NS sample."""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from pypixelgui import presets
from pypixelgui.commands import validate
from pypixelgui.live import events, ns, schedule

DATA = Path(__file__).parent / "data"
AMS = ZoneInfo("Europe/Amsterdam")


def sched(name):
    return schedule.parse_frab_xml((DATA / name).read_bytes())


def test_eth0_parse_and_now_next():
    s = sched("frab_eth0_2026.xml")
    assert s.title == "eth0:2026" and len(s.talks) == 21
    assert "Talks Room 2.25" in s.rooms
    entries = schedule.current_and_next(s, datetime(2026, 11, 7, 13, 40, tzinfo=AMS), 3)
    running = [t.title for t, now in entries if now]
    upcoming = [(t.start.strftime("%H:%M"), t.room) for t, now in entries if not now]
    assert running == ["saturday brunch"]
    assert upcoming[0] == ("14:00", "Talks Room 2.25")


def test_bornhack_local_times_despite_utc_stamp():
    s = sched("frab_bornhack_2026.xml")
    talk = next(t for t in s.talks if t.title == "WYHIWYG")
    # <date> says 20:00+00:00 but <start> is local 20:00 on a +02:00 day: show 20:00 local
    assert talk.start.strftime("%Y-%m-%d %H:%M %z") == "2026-07-15 20:00 +0200"


def test_room_filter_and_finished_event():
    s = sched("frab_eth0_2026.xml")
    only = schedule.current_and_next(s, datetime(2026, 11, 7, 8, 0, tzinfo=AMS), 10, rooms=["bar/lounge"])
    assert only and {t.room for t, _ in only} == {"Bar/Lounge"}
    after = datetime(2026, 11, 20, tzinfo=AMS)
    assert schedule.current_and_next(s, after) == []
    frames = schedule.frames(s, 64, 16, now=after, lang="en")
    assert frames  # shows "event finished"


@pytest.mark.parametrize("size", [(32, 16), (64, 16), (64, 32), (64, 64), (128, 32)])
def test_schedule_draws_on_every_panel(size):
    s = sched("frab_eth0_2026.xml")
    icon = Image.new("RGBA", (48, 48), (0, 200, 255, 255))
    frames = schedule.frames(s, *size, now=datetime(2026, 11, 7, 13, 40, tzinfo=AMS), icon=icon)
    assert frames and all(f.size == size for f, _ in frames)


def test_schedule_preset_uses_event_icon(monkeypatch):
    monkeypatch.setattr(schedule, "get_bytes", lambda *a, **k: (DATA / "frab_eth0_2026.xml").read_bytes())
    monkeypatch.setattr(events, "icon", lambda spec: Image.new("RGBA", (32, 32), (0, 255, 255, 255)))
    entry = {"name": "eth0", "steps": [["schedule", {"url": "https://x/schedule.xml", "icon": "eth0wiki:eth0_2026"}]]}
    p = presets.user_preset(entry)
    assert p.thumbnail().getpixel((24, 14)) == (0, 255, 255)  # button shows the event icon
    (cmd, params), = p.steps(64, 16)
    validate(cmd, params, remote=True, allow_destructive=False)


def test_icon_finder_prefers_largest_declared_icon():
    f = events._IconFinder()
    f.feed('<link rel="icon" sizes="16x16" href="/a.png"><link rel="apple-touch-icon" sizes="180x180" '
           'href="/big.png"><link rel="mask-icon" href="/m.svg"><img src="/images/Eth0-2026.png">')
    assert max(f.icons)[1] == "/big.png" and f.images == ["/images/Eth0-2026.png"]


def test_ns_departures_and_stations():
    deps = ns.parse_departures(json.loads((DATA / "ns_departures_sample.json").read_text()))
    assert [(d.line, d.destination, d.platform, d.platform_changed) for d in deps[:2]] == [
        ("SPR", "Amersfoort Centraal", "12", False), ("IC", "Den Haag Centraal", "5b", True)]
    assert deps[0].transport == "TRAIN" and deps[-1].cancelled  # departed train dropped
    assert deps[1].delay_min == 2
    stations = ns.parse_stations(json.loads((DATA / "ns_stations_sample.json").read_text()))
    assert [s.code for s in stations] == ["UT", "UTZL"]
    ns.set_api_key("")
    with pytest.raises(Exception, match="API key"):
        ns.fetch_departures("UT")


# --- CCC ------------------------------------------------------------------------------

BERLIN = ZoneInfo("Europe/Berlin")


def test_congress_39c3_parses_in_berlin_time():
    s = sched("frab_39c3_sample.xml")
    assert s.title == "39th Chaos Communications Congress" and len(s.talks) == 82
    assert all(t.start.utcoffset().total_seconds() == 3600 for t in s.talks)  # CET in December
    first_day = s.talks[0].start.astimezone(BERLIN).date().isoformat()
    assert first_day == "2025-12-27"


def test_camp23_parses():
    s = sched("frab_camp23_sample.xml")
    assert s.title == "Chaos Communication Camp 2023" and set(s.rooms) == {"Milliways", "Marktplatz"}
    assert all(t.start.utcoffset().total_seconds() == 7200 for t in s.talks)  # CEST in August


def test_announced_but_empty_congress_says_when_it_starts():
    s = sched("frab_40c3_empty.xml")
    assert s.talks == [] and str(s.start) == "2026-12-27"
    now = datetime(2026, 11, 9, 14, 0, tzinfo=AMS)
    assert schedule._status(s, now, "en") == "starts 27 Dec"
    assert schedule._status(s, now, "nl") == "begint 27 dec"


def test_ccc_editions_offline():
    from datetime import date
    congress = events.congress_editions(date(2026, 11, 9))
    assert [e.name for e in congress] == ["40C3 (2026)", "39C3 (2025)", "38C3 (2024)"]
    assert congress[1].schedule_url == "https://api.events.ccc.de/congress/2025/schedule.xml"
    camps = events.camp_editions(date(2026, 11, 9))
    assert [e.name for e in camps] == ["CCCamp 2027", "CCCamp 2023", "CCCamp 2019"]
    assert camps[1].schedule_url.endswith("/camp/2023/hub/api/c/camp23/schedule.xml")
    assert [e.name for e in events.camp_editions(date(2024, 1, 1))] == ["CCCamp 2023", "CCCamp 2019"]


def test_ccc_icon_candidates_follow_redirects(monkeypatch):
    # the congress page redirects to /infos/startpage.html and declares a relative icon
    monkeypatch.setattr(events, "get_page", lambda url, **k: (
        "https://events.ccc.de/congress/2026/infos/startpage.html",
        b'<link rel="icon" href="assets/favicon.svg">'))
    cands = events.icon_candidates("ccc:congress/2026")
    assert cands[0] == "https://events.ccc.de/congress/2026/hub/static/plainui/img/favicon.ico"
    assert "https://events.ccc.de/congress/2026/infos/assets/favicon.svg" in cands


def test_icon_loader_skips_broken_links_and_renders_svg(monkeypatch, tmp_path):
    pytest.importorskip("PySide6.QtSvg")
    svg = (b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
           b'<rect width="10" height="10" fill="#75b44b"/></svg>')
    pages = {"https://x/a.ico": b"<!DOCTYPE html><html>404</html>", "https://x/b.svg": svg}
    monkeypatch.setattr(events, "get_bytes", lambda url, **k: pages[url])
    monkeypatch.setattr(events, "icon_candidates", lambda spec: ["https://x/a.ico", "https://x/b.svg"])
    monkeypatch.setattr(events, "_icon_cache", lambda: tmp_path)
    im = events.icon("ccc:congress/2026")
    assert im is not None and im.getpixel((im.width // 2, im.height // 2))[:3] == (0x75, 0xB4, 0x4B)
