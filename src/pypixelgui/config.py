"""Settings stored as JSON in the platform's per-user config directory."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "pypixelgui"


def default_path() -> Path:
    return config_dir() / "config.json"


def media_dir() -> Path:
    """Copies of images saved as presets."""
    return config_dir() / "media"


def slugify(name: str) -> str:
    """Make a string safe for use as an MQTT topic level."""
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip()).strip("_").lower()
    return s or "display"


@dataclass
class DeviceEntry:
    address: str
    name: str = ""
    #: Topic segment used by MQTT. Only devices listed here are reachable remotely.
    alias: str = ""
    #: Keep a connection open on start-up so nobody else can connect.
    hold: bool = False
    #: "ipixel" (iPixel Color) or "iledcolor".
    kind: str = "ipixel"
    #: iLEDColor: geometry/features read from the advertisement.
    panel: dict | None = None
    #: iLEDColor: the display's 6-character password, if it has one (stored in plain text).
    password: str = ""

    def __post_init__(self):
        if not self.alias:
            self.alias = slugify(self.name or self.address)


@dataclass
class Settings:
    devices: list[DeviceEntry] = field(default_factory=list)
    #: Saved presets: ``{"name", "steps": [[command, params], ...]}``.
    presets: list[dict] = field(default_factory=list)
    #: Saved rotations, see :mod:`pypixelgui.rotation`.
    rotations: list[dict] = field(default_factory=list)
    #: Display groups (several panels as one), see :mod:`pypixelgui.groups`.
    groups: list[dict] = field(default_factory=list)
    #: Settings owned by add-ons (e.g. ``"mqtt"`` for pypixelgui-mqtt). Kept as
    #: plain dicts so the core app round-trips them without knowing their shape.
    plugins: dict[str, dict] = field(default_factory=dict)
    #: Only connect to devices listed in `devices` (scan results are still shown).
    known_devices_only: bool = False

    def find(self, address: str) -> DeviceEntry | None:
        return next((d for d in self.devices if d.address.lower() == address.lower()), None)

    def by_alias(self, alias: str) -> DeviceEntry | None:
        return next((d for d in self.devices if d.alias == alias), None)

    def group_by_alias(self, alias: str) -> dict | None:
        return next((g for g in self.groups if g.get("alias") == alias), None)

    def taken_aliases(self) -> set[str]:
        return {d.alias for d in self.devices} | {g.get("alias") for g in self.groups}

    def upsert(self, entry: DeviceEntry) -> DeviceEntry:
        existing = self.find(entry.address)
        if existing:
            existing.name = entry.name or existing.name
            return existing
        taken = {d.alias for d in self.devices}
        base, n = entry.alias, 2
        while entry.alias in taken:
            entry.alias = f"{base}_{n}"
            n += 1
        self.devices.append(entry)
        return entry

    # --- persistence ----------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or default_path()
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            devices=[DeviceEntry(**{k: v for k, v in d.items() if k in DeviceEntry.__dataclass_fields__})
                     for d in raw.get("devices", [])],
            presets=list(raw.get("presets", [])),
            rotations=list(raw.get("rotations", [])),
            groups=list(raw.get("groups", [])),
            plugins=dict(raw.get("plugins", {})),
            known_devices_only=bool(raw.get("known_devices_only", False)),
        )

    def save(self, path: Path | None = None) -> Path:
        path = path or default_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        if os.name == "posix":  # add-ons may store credentials here
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        return path
