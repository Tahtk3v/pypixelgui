"""Weather (Open-Meteo, real response) and transit (OVapi, sample in the documented format)."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from pypixelgui import presets
from pypixelgui.commands import validate
from pypixelgui.live import http, transit, weather

DATA = Path(__file__).parent / "data"


def load(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def test_weather_parse_and_draw():
    w = weather.parse_forecast(load("openmeteo_forecast.json"))
    assert (w.temperature, w.code, len(w.days)) == (10.0, 0, 3)
    assert w.days[2].rain_chance == 73 and weather.kind_of(w.days[2].code) == "rain"
    for size in [(32, 16), (64, 16), (96, 32)]:
        frames = weather.frames(w, *size, place="Utrecht")
        assert all(f.size == size for f, _ in frames)
        assert any(f.getbbox() for f, _ in frames)


def test_geocode_parse():
    places = weather.parse_places(load("openmeteo_geocode.json"))
    assert places[0].name == "Den Haag" and abs(places[0].latitude - 52.07667) < 1e-4


@pytest.mark.parametrize("code,kind", [(0, "clear"), (2, "partly"), (3, "cloudy"), (45, "fog"),
                                       (61, "rain"), (81, "rain"), (73, "snow"), (95, "storm")])
def test_weather_codes(code, kind):
    assert weather.kind_of(code) == kind
    assert weather.icon(code, 16).getbbox()


NOW = datetime(2026, 11, 9, 13, 33, 10, tzinfo=transit.TZ)


def test_departures_parse_dedupe_filter():
    deps = transit.parse_departures(load("ovapi_stoparea_sample.json"), now=NOW)
    assert [d.line for d in deps] == ["22", "28", "12", "8"]  # sorted, PASSED dropped, 12 once
    assert deps[2].delay_min == 3 and deps[3].cancelled and deps[0].transport == "TRAM"
    only12 = transit.parse_departures(load("ovapi_stoparea_sample.json"), lines=["12"], now=NOW)
    assert [d.line for d in only12] == ["12"]
    later = transit.parse_departures(load("ovapi_stoparea_sample.json"), now=NOW.replace(hour=14))
    assert later == []


def test_tpc_reply_shape_also_works():
    data = load("ovapi_stoparea_sample.json")["utcsbu"]  # {tpc: {...}} like /tpc/<code>
    assert [d.line for d in transit.parse_departures(data, now=NOW)] == ["22", "28", "12", "8"]


def test_stop_search():
    stops = [transit.Stop("a", "Utrecht, Centraal Station", "Utrecht"),
             transit.Stop("b", "Amsterdam, Centraal Station", "Amsterdam"),
             transit.Stop("c", "Utrecht, Vaartsche Rijn", "Utrecht")]
    assert [s.code for s in transit.search_stops(stops, "centraal utrecht")] == ["a"]
    assert transit.search_stops(stops, "   ") == []


def test_transit_draw_and_empty():
    deps = transit.parse_departures(load("ovapi_stoparea_sample.json"), now=NOW)
    assert transit.frames(deps, 64, 16, "CS", now=NOW)
    assert transit.frames([], 64, 16)  # "geen vertrektijden"


def test_live_presets_build_valid_uploads(monkeypatch):
    def fake_get_json(url, params=None, **kw):
        if "open-meteo" in url:
            return load("openmeteo_forecast.json")
        return load("ovapi_stoparea_sample.json")
    monkeypatch.setattr(http, "get_json", fake_get_json)
    monkeypatch.setattr(weather, "get_json", fake_get_json)
    monkeypatch.setattr(transit, "get_json", fake_get_json)
    for entry in (
        {"name": "Weer", "steps": [["weather", {"latitude": 52.09, "longitude": 5.12, "label": "Utrecht"}]]},
        {"name": "Bus", "steps": [["transit", {"stop": "utcsbu", "label": "CS", "count": 2}]]},
    ):
        p = presets.user_preset(entry)
        (cmd, params), = p.steps(64, 16)
        validate(cmd, params, remote=True, allow_destructive=False)
        assert p.thumbnail().size == (48, 28)


def test_http_cache_and_error(monkeypatch):
    calls = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return b'{"ok": 1}'

    monkeypatch.setattr(http.urllib.request, "urlopen", lambda req, timeout: calls.append(req) or Resp())
    http.clear_cache()
    assert http.get_json("https://x.test/a", ttl=60) == {"ok": 1}
    assert http.get_json("https://x.test/a", ttl=60) == {"ok": 1}
    assert len(calls) == 1

    def boom(req, timeout):
        raise OSError("down")
    monkeypatch.setattr(http.urllib.request, "urlopen", boom)
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    with pytest.raises(http.FetchError, match="x.test"):
        http.get_json("https://x.test/b")


@pytest.mark.parametrize("size", [(64, 16), (128, 32)])
def test_scroll_speed_is_characters_per_second_on_any_panel(size):
    from pypixelgui import render
    deps = transit.parse_departures(load("ovapi_stoparea_sample.json"), now=NOW)
    font = render.font_for(*size)
    for name, cps in render.SCROLL_SPEEDS.items():
        frames = transit.frames(deps, *size, now=NOW, speed=name, layout="line")
        lead = frames[0][1] - frames[1][1]  # the pause on the first frame isn't scrolling
        seconds = (sum(d for _, d in frames) - lead) / 1000
        travelled = render._strip(transit.segments(deps, size[1], count=3, now=NOW, size=font), size[1], 4).width
        chars_per_s = travelled / seconds / (font / 2)
        assert abs(chars_per_s - cps) / cps < 0.25, (name, chars_per_s)


def test_departures_show_transport_pictogram():
    deps = transit.parse_departures(load("ovapi_stoparea_sample.json"), now=NOW)
    segs = transit.segments(deps[:1], 16, now=NOW)
    icon = transit.transport_icon("TRAM", 16)
    assert segs[0].tobytes() == icon.tobytes()  # tram 22 starts with the tram pictogram
    for kind in ("BUS", "TRAM", "METRO", "BOAT", "TRAIN"):
        assert transit.transport_icon(kind, 16).getbbox()


def test_scroll_starts_with_text_on_screen():
    from pypixelgui import render
    deps = transit.parse_departures(load("ovapi_stoparea_sample.json"), now=NOW)
    frames = transit.frames(deps, 64, 16, now=NOW, layout="line")
    first, ms = frames[0]
    assert first.getbbox() is not None and first.getbbox()[0] <= 2  # text at the left edge, not blank
    assert ms >= 1000  # held so the beginning can be read
