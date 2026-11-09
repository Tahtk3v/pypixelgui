"""Dutch trains from the NS API (Reisinformatie API) – departures with platforms.

Needs a free personal key: register at https://apiportal.ns.nl, subscribe to
"Ns-App" (Reisinformatie API) and paste the primary key in the Live tab.
"""

from __future__ import annotations

from datetime import datetime

from .http import FetchError, get_json
from .transit import TZ, Departure, Stop

BASE = "https://gateway.apiportal.ns.nl/reisinformatie-api/api/v2"
CACHE_SECONDS = 30
_key = ""


def set_api_key(key: str) -> None:
    global _key
    _key = (key or "").strip()


def has_key() -> bool:
    return bool(_key)


def _get(path: str, params: dict, ttl: float = 0):
    if not _key:
        raise FetchError("NS trains need an NS API key (Live tab → NS API key)")
    return get_json(f"{BASE}/{path}", params, ttl=ttl, headers={"Ocp-Apim-Subscription-Key": _key})


def search_stations(query: str, limit: int = 15) -> list[Stop]:
    return parse_stations(_get("stations", {"q": query, "limit": limit}, ttl=3600))[:limit]


def parse_stations(data: dict) -> list[Stop]:
    """v2 (``code``/``namen``) and v3 (``id.code``/``names``) shapes."""
    out = []
    for s in (data or {}).get("payload", []) or []:
        code = s.get("code") or (s.get("id") or {}).get("code")
        names = s.get("namen") or s.get("names") or {}
        name = names.get("lang") or names.get("long") or names.get("middel") or names.get("medium") or code
        if code:
            out.append(Stop(str(code), f"{name} (NS)", ""))
    return out


def fetch_departures(station: str, lines: list[str] | None = None) -> list[Departure]:
    data = _get("departures", {"station": station, "maxJourneys": 25}, ttl=CACHE_SECONDS)
    return parse_departures(data, lines)


def _dt(value: str) -> datetime:
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M%z"):
        try:
            return datetime.strptime(value, fmt).astimezone(TZ)
        except ValueError:
            continue
    return datetime.fromisoformat(value).astimezone(TZ)


def parse_departures(data: dict, lines: list[str] | None = None) -> list[Departure]:
    wanted = {x.strip().upper() for x in lines or [] if x.strip()}
    out = []
    for d in ((data or {}).get("payload") or {}).get("departures", []) or []:
        if d.get("departureStatus") == "DEPARTED":
            continue
        product = d.get("product") or {}
        category = product.get("categoryCode") or d.get("trainCategory") or "TRN"
        if wanted and category.upper() not in wanted and str(product.get("number", "")).upper() not in wanted:
            continue
        planned = _dt(d["plannedDateTime"])
        actual = _dt(d.get("actualDateTime") or d["plannedDateTime"])
        planned_track, actual_track = d.get("plannedTrack") or "", d.get("actualTrack") or ""
        out.append(Departure(category, d.get("direction", ""), actual, planned, "TRAIN", bool(d.get("cancelled")),
                             actual_track or planned_track,
                             bool(actual_track and planned_track and actual_track != planned_track)))
    return sorted(out, key=lambda x: x.expected)
