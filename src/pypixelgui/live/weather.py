"""Weather from Open-Meteo (free, no API key; data CC BY 4.0 – "Weather data by Open-Meteo.com").

Shows the current temperature and conditions, optionally followed by a few days
of forecast, as a still image when it fits the display or a scrolling one when not.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from PIL import Image, ImageDraw

from .. import render
from .http import get_json

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
CACHE_SECONDS = 600  # Open-Meteo updates every 15 minutes; don't ask more often than needed

DAYS = {"nl": ["ma", "di", "wo", "do", "vr", "za", "zo"], "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}


@dataclass(frozen=True)
class Place:
    name: str
    latitude: float
    longitude: float
    region: str = ""
    country: str = ""

    def label(self) -> str:
        return ", ".join(x for x in (self.name, self.region, self.country) if x)


@dataclass(frozen=True)
class Day:
    day: date
    code: int
    high: float
    low: float
    rain_chance: int | None


@dataclass(frozen=True)
class Weather:
    temperature: float
    code: int
    is_day: bool
    wind_kmh: float
    days: list[Day]


def search_places(name: str, country: str | None = "NL", language: str = "nl", count: int = 8) -> list[Place]:
    params = {"name": name, "count": count, "language": language, "format": "json"}
    if country:
        params["countryCode"] = country
    data = get_json(GEOCODE_URL, params, ttl=86400)
    return parse_places(data)


def parse_places(data: dict) -> list[Place]:
    return [Place(r["name"], float(r["latitude"]), float(r["longitude"]), r.get("admin1", ""),
                  r.get("country", r.get("country_code", "")))
            for r in (data or {}).get("results", []) or []]


def fetch(latitude: float, longitude: float, days: int = 3) -> Weather:
    data = get_json(FORECAST_URL, {
        "latitude": round(latitude, 3), "longitude": round(longitude, 3), "timezone": "auto",
        "current": "temperature_2m,weather_code,wind_speed_10m,is_day",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "forecast_days": max(1, min(7, days + 1)),
    }, ttl=CACHE_SECONDS)
    return parse_forecast(data)


def parse_forecast(data: dict) -> Weather:
    cur = data["current"]
    daily = data.get("daily", {})
    out = []
    rain = daily.get("precipitation_probability_max") or [None] * len(daily.get("time", []))
    for t, c, hi, lo, pr in zip(daily.get("time", []), daily.get("weather_code", []),
                               daily.get("temperature_2m_max", []), daily.get("temperature_2m_min", []), rain):
        out.append(Day(date.fromisoformat(t), int(c or 0), float(hi), float(lo), None if pr is None else int(pr)))
    return Weather(float(cur["temperature_2m"]), int(cur.get("weather_code") or 0), bool(cur.get("is_day", 1)),
                   float(cur.get("wind_speed_10m") or 0), out)


# --- icons (drawn, so they stay crisp at any size) ------------------------------------

def kind_of(code: int) -> str:
    """WMO weather code → icon name."""
    if code == 0:
        return "clear"
    if code in (1, 2):
        return "partly"
    if code == 3:
        return "cloudy"
    if code in (45, 48):
        return "fog"
    if 51 <= code <= 67 or 80 <= code <= 82:
        return "rain"
    if 71 <= code <= 77 or code in (85, 86):
        return "snow"
    if code >= 95:
        return "storm"
    return "cloudy"


def icon(code: int, size: int, is_day: bool = True) -> Image.Image:
    s = size
    im = Image.new("RGB", (s, s))
    d = ImageDraw.Draw(im)
    k = kind_of(code)
    sun, cloud, dark = (255, 200, 0), (200, 200, 210), (130, 130, 140)

    def draw_sun(cx, cy, r):
        if not is_day:
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(230, 230, 180))
            d.ellipse([cx - r + r // 2, cy - r - 1, cx + r + r // 2, cy + r - 1], fill=(0, 0, 0))
            return
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=sun)
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0), (1, 1), (-1, -1), (1, -1), (-1, 1)):
            d.point((cx + dx * (r + 2), cy + dy * (r + 2)), fill=sun)

    def draw_cloud(y0, colour=cloud):
        d.ellipse([s * 0.10, y0 + s * 0.10, s * 0.55, y0 + s * 0.45], fill=colour)
        d.ellipse([s * 0.35, y0, s * 0.85, y0 + s * 0.45], fill=colour)
        d.rectangle([s * 0.15, y0 + s * 0.25, s * 0.85, y0 + s * 0.45], fill=colour)

    if k == "clear":
        draw_sun(s // 2, s // 2, max(2, s // 4))
    elif k == "partly":
        draw_sun(int(s * 0.35), int(s * 0.35), max(2, s // 5))
        draw_cloud(s * 0.35)
    elif k == "cloudy":
        draw_cloud(s * 0.25)
    elif k == "fog":
        for i, y in enumerate((0.3, 0.5, 0.7)):
            d.line([s * (0.1 + 0.1 * (i % 2)), s * y, s * (0.9 - 0.1 * (i % 2)), s * y], fill=cloud,
                   width=max(1, s // 10))
    else:
        draw_cloud(s * 0.1, dark if k == "storm" else cloud)
        if k == "rain":
            for x in (0.3, 0.5, 0.7):
                d.line([s * x, s * 0.65, s * (x - 0.08), s * 0.9], fill=(60, 140, 255), width=max(1, s // 12))
        elif k == "snow":
            for x, y in ((0.3, 0.7), (0.55, 0.85), (0.75, 0.68)):
                d.point((s * x, s * y), fill=(255, 255, 255))
        else:
            d.polygon([(s * 0.55, s * 0.55), (s * 0.4, s * 0.8), (s * 0.52, s * 0.8), (s * 0.45, s),
                       (s * 0.7, s * 0.7), (s * 0.57, s * 0.7), (s * 0.65, s * 0.55)], fill=sun)
    return im


def temp_colour(t: float) -> str:
    if t < 0:
        return "66ccff"
    if t < 10:
        return "ffffff"
    if t < 20:
        return "ffdd55"
    if t < 27:
        return "ffaa33"
    return "ff5533"


def segments(w: Weather, place: str = "", forecast_days: int = 2, lang: str = "nl",
             height: int = 16) -> list[Image.Image]:
    size = render.segment_font_size(height)
    segs: list[Image.Image] = []
    if place:
        segs.append(render.text_segment(place, "aaaaaa", height, size))
    segs.append(icon(w.code, min(height, size), w.is_day))
    segs.append(render.text_segment(f"{w.temperature:.0f}°", temp_colour(w.temperature), height, size))
    names = DAYS.get(lang, DAYS["en"])
    for day in w.days[1:1 + max(0, forecast_days)]:
        segs.append(render.text_segment(names[day.day.weekday()], "888888", height, size))
        segs.append(icon(day.code, min(height, size)))
        txt = f"{day.high:.0f}/{day.low:.0f}°"
        if day.rain_chance is not None and day.rain_chance >= 30:
            txt += f" {day.rain_chance}%"
        segs.append(render.text_segment(txt, temp_colour(day.high), height, size))
    return segs


def frames(w: Weather, width: int, height: int, place: str = "", forecast_days: int = 2,
           lang: str = "nl") -> list[render.Frame]:
    return render.marquee(segments(w, place, forecast_days, lang, height), width, height)
