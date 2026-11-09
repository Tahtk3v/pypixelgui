"""Tiny HTTP helper with a short in-memory cache (no extra dependencies)."""

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


def get_bytes(url: str, params: dict | None = None, *, ttl: float = 0, timeout: float = 15,
              retries: int = 2, headers: dict | None = None) -> bytes:
    """GET ``url``; the body is reused for ``ttl`` seconds. Follows redirects."""
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
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
            break
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise FetchError(f"{urllib.parse.urlsplit(url).netloc} refused access ({e.code}) – "
                                 "is the API key right?") from e
            if 400 <= e.code < 500:
                raise FetchError(f"{urllib.parse.urlsplit(url).netloc}: {e.code} {e.reason}") from e
            last = e
        except (urllib.error.URLError, OSError) as e:
            last = e
        time.sleep(0.5 * (attempt + 1))
    else:
        raise FetchError(f"could not load {urllib.parse.urlsplit(url).netloc}: {last}") from last
    with _lock:
        _cache[url] = (now, body)
    return body


def get_json(url: str, params: dict | None = None, *, ttl: float = 0, timeout: float = 15,
             retries: int = 2, headers: dict | None = None) -> object:
    body = get_bytes(url, params, ttl=ttl, timeout=timeout, retries=retries,
                     headers={"Accept": "application/json", **(headers or {})})
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError as e:
        raise FetchError(f"{urllib.parse.urlsplit(url).netloc} did not return JSON") from e


def clear_cache() -> None:
    with _lock:
        _cache.clear()
