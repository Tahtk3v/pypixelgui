"""Known hacker events and their schedule URLs, plus site icons.

* BornHack – one camp a year: https://bornhack.dk/bornhack-YYYY/program/frab.xml
* eth0 – frab at content.eth0.nl: /en/<edition>/public/schedule.xml; its logo
  comes from wiki.eth0.nl (the schedule site only has frab's own icon)
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

from .http import FetchError, get_bytes


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


KNOWN = {"BornHack": bornhack_editions, "eth0": eth0_editions}


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
    im = Image.open(io.BytesIO(get_bytes(url, ttl=86400, timeout=20)))
    if getattr(im, "n_frames", 1) > 1 or im.format == "ICO":
        sizes = sorted(getattr(im, "info", {}).get("sizes") or [im.size])
        if im.format == "ICO" and sizes:
            im.size = sizes[-1]
    return im.convert("RGBA")


def find_icon_url(spec: str) -> str | None:
    """Turn an icon spec into an image URL: a direct URL, ``eth0wiki:<edition>``,
    or a website whose page is searched for icons."""
    if spec.startswith("eth0wiki:"):
        year = re.findall(r"(\d{4})", spec)
        html = get_bytes("https://wiki.eth0.nl/index.php?title=Main_Page", ttl=86400).decode("utf-8", "replace")
        finder = _IconFinder()
        finder.feed(html)
        for src in finder.images:
            name = src.rsplit("/", 1)[-1].lower()
            if name.startswith("eth0") and (not year or year[0] in name):
                return urllib.parse.urljoin("https://wiki.eth0.nl/", src)
        return None
    if re.search(r"\.(png|ico|jpe?g|gif|webp)(\?|$)", spec, re.I):
        return spec
    html = get_bytes(spec, ttl=86400).decode("utf-8", "replace")
    finder = _IconFinder()
    finder.feed(html)
    if finder.icons:
        return urllib.parse.urljoin(spec, max(finder.icons)[1])
    return urllib.parse.urljoin(spec, "/favicon.ico")


def icon(spec: str) -> Image.Image | None:
    """The event's icon (cached on disk), or None if there isn't one."""
    if not spec:
        return None
    path = _icon_cache() / (hashlib.sha1(spec.encode()).hexdigest()[:16] + ".png")
    if path.exists():
        try:
            return Image.open(path).convert("RGBA")
        except OSError:
            pass
    try:
        url = find_icon_url(spec)
        if not url:
            return None
        im = _load_image(url)
    except (FetchError, OSError, ValueError):
        return None
    im.thumbnail((64, 64), Image.LANCZOS)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)
    return im
