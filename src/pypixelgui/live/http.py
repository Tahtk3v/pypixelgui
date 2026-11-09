"""Tiny JSON-over-HTTP helper with a short in-memory cache (no extra dependencies)."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "pypixelgui (+https://github.com/Tahtk3v/pypixelgui)"

_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()


class FetchError(RuntimeError):
    pass


def get_json(url: str, params: dict | None = None, *, ttl: float = 0, timeout: float = 15,
             retries: int = 2) -> object:
    """GET ``url`` and decode JSON. Results are reused for ``ttl`` seconds."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    now = time.monotonic()
    with _lock:
        hit = _cache.get(url)
        if hit and now - hit[0] < ttl:
            return hit[1]
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            break
        except (urllib.error.URLError, OSError, ValueError) as e:
            last = e
            time.sleep(0.5 * (attempt + 1))
    else:
        raise FetchError(f"could not load {urllib.parse.urlsplit(url).netloc}: {last}") from last
    with _lock:
        _cache[url] = (now, data)
    return data


def clear_cache() -> None:
    with _lock:
        _cache.clear()
