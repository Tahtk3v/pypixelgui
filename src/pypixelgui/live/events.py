"""Known hacker events and their schedule URLs, plus site icons.

* BornHack – one camp a year: https://bornhack.dk/bornhack-YYYY/program/frab.xml
* eth0 – frab at content.eth0.nl: /en/<edition>/public/schedule.xml; its logo
  comes from wiki.eth0.nl (the schedule site only has frab's own icon)
* CCC – Congress (``api.events.ccc.de/congress/YYYY/schedule.xml``, e.g. 39C3 = 2025)
  and CCCamp (the event "hub" export); icons come from each event's hub/site
* Anything else: paste a frab/pretalx ``schedule.xml`` URL.
"""

from __future__ import annotations

import hashlib
import io
import re
import urllib.parse
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image

from datetime import date

from .http import FetchError, get_bytes, get_page


@dataclass(frozen=True)
class Edition:
    event: str        # "BornHack"
    name: str         # "BornHack 2026"
    schedule_url: str
    icon_url: str = ""
    website: str = ""


# --- discovery ---------------------------------------------------------------------

def bornhack_editions() -> list[Edition]:
    html = get_bytes("https://bornhack.dk/", ttl=3600).decode("utf-8", "replace")
    years = sorted({int(y) for y in re.findall(r"/bornhack-(\d{4})/", html)}, reverse=True)
    icon = "https://bornhack.dk/static/img/favicon/apple-touch-icon.png"
    return [Edition("BornHack", f"BornHack {y}", f"https://bornhack.dk/bornhack-{y}/program/frab.xml",
                    icon, f"https://bornhack.dk/bornhack-{y}/") for y in years]


def eth0_editions() -> list[Edition]:
    seen: dict[str, None] = {}
    for page in ("https://content.eth0.nl/", "https://content.eth0.nl/en/conferences/past"):
        try:
            html = get_bytes(page, ttl=3600).decode("utf-8", "replace")
        except FetchError:
            continue
        for acr in re.findall(r'href="/en/([^"/]+)/public/schedule', html):
            seen.setdefault(acr)
    out = []
    for acr in sorted(seen, reverse=True):
        nice = acr.replace("eth0_", "eth0:").replace("_", " ")
        out.append(Edition("eth0", nice, f"https://content.eth0.nl/en/{acr}/public/schedule.xml",
                           f"eth0wiki:{acr}", "https://eth0.nl/"))
    return out


def congress_editions(today: date | None = None) -> list[Edition]:
    """Chaos Communication Congress, newest first. The yearly number is year − 1986
    (39C3 = 2025). Schedules from 2024 on are on api.events.ccc.de."""
    today = today or date.today()
    out = []
    for year in range(today.year, 2023, -1):
        n = year - 1986
        out.append(Edition("Congress", f"{n}C3 ({year})", f"https://api.events.ccc.de/congress/{year}/schedule.xml",
                           f"ccc:congress/{year}", f"https://events.ccc.de/congress/{year}/"))
    return out


_CAMP_SCHEDULES = {
    2023: "https://events.ccc.de/camp/2023/hub/api/c/camp23/schedule.xml",
    2019: "https://fahrplan.events.ccc.de/camp/2019/Fahrplan/schedule.xml",
}


def camp_editions(today: date | None = None) -> list[Edition]:
    """Chaos Communication Camp (every four years). The next camp's schedule is
    expected at the same kind of hub address as 2023's."""
    today = today or date.today()
    years = dict(_CAMP_SCHEDULES)
    nxt = 2027
    while nxt <= today.year + 1:
        years.setdefault(nxt, f"https://events.ccc.de/camp/{nxt}/hub/api/c/camp{nxt % 100}/schedule.xml")
        nxt += 4
    return [Edition("CCCamp", f"CCCamp {y}", url, f"ccc:camp/{y}", f"https://events.ccc.de/camp/{y}/")
            for y, url in sorted(years.items(), reverse=True)]


KNOWN = {"BornHack": bornhack_editions, "eth0": eth0_editions, "CCC Congress": congress_editions,
         "CCCamp": camp_editions}


# --- icons -----------------------------------------------------------------------------

class _IconFinder(HTMLParser):
    def __init__(self):
        super().__init__()
        self.icons: list[tuple[int, str]] = []
        self.images: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        rel = (a.get("rel") or "").lower()
        if tag == "link" and "icon" in rel and a.get("href") and "mask-icon" not in rel:
            size = max([int(x) for x in re.findall(r"(\d+)x\d+", a.get("sizes") or "")] or
                       [180 if "apple" in rel else 16])
            self.icons.append((size, a["href"]))
        elif tag == "meta" and (a.get("property") or "").lower() == "og:image" and a.get("content"):
            self.icons.append((64, a["content"]))
        elif tag == "img" and a.get("src"):
            self.images.append(a["src"])


def _icon_cache() -> Path:
    from ..config import config_dir
    return config_dir() / "icons"


def _load_image(url: str) -> Image.Image:
    data = get_bytes(url, ttl=86400, timeout=20)
    head = data[:300].lstrip().lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:2000].lower()):
        return _render_svg(data)
    if head.startswith((b"<!doctype", b"<html")):
        raise ValueError("not an image")
    im = Image.open(io.BytesIO(data))
    if im.format == "ICO":
        sizes = sorted(getattr(im, "info", {}).get("sizes") or [im.size])
        im.size = sizes[-1]
    return im.convert("RGBA")


def _render_svg(data: bytes, size: int = 64) -> Image.Image:
    """Rasterise an SVG icon with Qt (PySide6 is already a dependency)."""
    from PySide6.QtCore import QByteArray
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    renderer = QSvgRenderer(QByteArray(data))
    if not renderer.isValid():
        raise ValueError("unreadable SVG")
    w, h = renderer.defaultSize().width() or size, renderer.defaultSize().height() or size
    scale = size / max(w, h)
    img = QImage(max(1, round(w * scale)), max(1, round(h * scale)), QImage.Format_RGBA8888)
    img.fill(0)
    painter = QPainter(img)
    renderer.render(painter)
    painter.end()
    return Image.frombuffer("RGBA", (img.width(), img.height()), bytes(img.constBits()), "raw", "RGBA",
                            img.bytesPerLine(), 1).copy()


def _page_icons(url: str) -> list[str]:
    """Icons a web page declares (biggest first), resolved against the page's final
    address after redirects, plus /favicon.ico as a last resort."""
    final, body = get_page(url, ttl=86400)
    finder = _IconFinder()
    finder.feed(body.decode("utf-8", "replace"))
    urls = [urllib.parse.urljoin(final, href) for _size, href in sorted(finder.icons, reverse=True)]
    return urls + [urllib.parse.urljoin(final, "/favicon.ico")]


def icon_candidates(spec: str) -> list[str]:
    """Image URLs to try, best first, for an icon spec: a direct image URL,
    ``eth0wiki:<edition>``, ``ccc:<congress|camp>/<year>``, or any website."""
    if spec.startswith("eth0wiki:"):
        year = re.findall(r"(\d{4})", spec)
        html = get_bytes("https://wiki.eth0.nl/index.php?title=Main_Page", ttl=86400).decode("utf-8", "replace")
        finder = _IconFinder()
        finder.feed(html)
        return [urllib.parse.urljoin("https://wiki.eth0.nl/", src) for src in finder.images
                if src.rsplit("/", 1)[-1].lower().startswith("eth0")
                and (not year or year[0] in src.rsplit("/", 1)[-1])][:1]
    if spec.startswith("ccc:"):
        path = spec[4:].strip("/")
        hub = f"https://events.ccc.de/{path}/hub/static/plainui/img/favicon.ico"
        try:
            return [hub] + _page_icons(f"https://events.ccc.de/{path}/")
        except FetchError:
            return [hub]
    if re.search(r"\.(png|ico|jpe?g|gif|webp|svg)(\?|$)", spec, re.I):
        return [spec]
    return _page_icons(spec)


def find_icon_url(spec: str) -> str | None:
    """First candidate icon URL that actually loads as an image."""
    for url in icon_candidates(spec):
        try:
            _load_image(url)
            return url
        except (FetchError, OSError, ValueError, ImportError):
            continue
    return None


NEGATIVE_CACHE_SECONDS = 86400


def icon(spec: str, fetch: bool = True) -> Image.Image | None:
    """The event's icon, from the disk cache; with ``fetch`` (default) it's looked up
    online when not cached. Lookups that found nothing are remembered for a day,
    so a missing icon doesn't cause a network round-trip every time.

    Never call with ``fetch=True`` from the GUI thread – it can take seconds."""
    if not spec:
        return None
    key = hashlib.sha1(spec.encode()).hexdigest()[:16]
    path = _icon_cache() / f"{key}.png"
    missing = _icon_cache() / f"{key}.none"
    if path.exists():
        try:
            return Image.open(path).convert("RGBA")
        except OSError:
            pass
    if not fetch:
        return None
    import time
    if missing.exists() and time.time() - missing.stat().st_mtime < NEGATIVE_CACHE_SECONDS:
        return None
    im = None
    try:
        for url in icon_candidates(spec):
            try:
                im = _load_image(url)
                break
            except (FetchError, OSError, ValueError, ImportError):
                continue
    except (FetchError, OSError, ValueError):
        im = None
    path.parent.mkdir(parents=True, exist_ok=True)
    if im is None:
        missing.touch()
        return None
    im.thumbnail((64, 64), Image.LANCZOS)
    im.save(path)
    missing.unlink(missing_ok=True)
    return im


def is_cached(spec: str) -> bool:
    key = hashlib.sha1(spec.encode()).hexdigest()[:16]
    return (_icon_cache() / f"{key}.png").exists() or (_icon_cache() / f"{key}.none").exists()
