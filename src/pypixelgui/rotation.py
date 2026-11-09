"""Saved rotations: an ordered list of presets, each shown for some seconds.

Stored in the settings as::

    {"name": "Shop window", "loop": true, "preload_slot": null,
     "items": [{"preset": "CYBER", "seconds": 10}, {"preset": "Trans flag", "seconds": 5}]}
"""

from __future__ import annotations

from . import presets as presets_mod

DEFAULT_SECONDS = 10


def resolve(entry: dict, saved_presets: list[dict] | None = None) -> list[tuple[presets_mod.Preset, float]]:
    """Turn a stored rotation into ``(preset, seconds)`` pairs; unknown presets raise ``KeyError``."""
    items = []
    for item in entry.get("items", []):
        preset = presets_mod.find(str(item.get("preset", "")), saved_presets)
        if preset is None:
            raise KeyError(f"rotation {entry.get('name')!r}: unknown preset {item.get('preset')!r}")
        seconds = float(item.get("seconds", DEFAULT_SECONDS))
        if not 1 <= seconds <= 86400:
            raise ValueError(f"rotation {entry.get('name')!r}: seconds must be between 1 and 86400")
        items.append((preset, seconds))
    if not items:
        raise ValueError(f"rotation {entry.get('name')!r} is empty")
    return items


def find(name: str, rotations: list[dict]) -> dict | None:
    key = name.strip().lower()
    return next((r for r in rotations if str(r.get("name", "")).lower() == key), None)
