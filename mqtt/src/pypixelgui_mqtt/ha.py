"""Home Assistant MQTT discovery.

Each display (and each display group) becomes an HA device with:

* **light** – on/off, brightness, and *effect* = any preset (CYBER, flags, weather, …)
* **text** "Message" – type something, it shows on the display
* **select** "Preset" and "Rotation"
* **switch** "Hold connection"
* **binary_sensor** "Bluetooth" (connectivity, diagnostic)

Commands use the zigbee2mqtt-style JSON topic ``<base>/<alias>/set`` and state is
published as JSON on ``<base>/<alias>`` (retained), so the same topics work for
Node-RED and friends. Entities are available while the bridge is online; a
display that isn't connected is connected automatically when commanded.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from . import __version__

NO_ROTATION = "—"


@dataclass(frozen=True)
class Target:
    alias: str
    name: str
    model: str  # "iPixel Color", "iLEDColor 64×16", "Group of 2 displays", …
    manufacturer: str = "pypixelgui"


def configs(target: Target, base: str, prefix: str, presets: list[str], rotations: list[str]) -> dict[str, dict]:
    """Discovery topic → config payload for one display."""
    a = target.alias
    state = f"{base}/{a}"
    device = {"identifiers": [f"pypixelgui_{a}"], "name": target.name, "model": target.model,
              "manufacturer": target.manufacturer}
    origin = {"name": "pypixelgui-mqtt", "sw_version": __version__,
              "support_url": "https://github.com/Tahtk3v/pypixelgui"}
    avail = {"availability": [{"topic": f"{base}/bridge/status"}], "device": device, "origin": origin}
    node = f"pypixelgui_{a}"

    def uid(obj: str) -> str:
        return f"{node}_{obj}"

    out = {
        f"{prefix}/light/{node}/light/config": {
            **avail, "name": None, "unique_id": uid("light"), "schema": "json",
            "command_topic": f"{state}/set", "state_topic": state,
            "brightness": True, "brightness_scale": 255,
            "effect": bool(presets), "effect_list": presets,
            "supported_color_modes": ["brightness"],
        },
        f"{prefix}/text/{node}/message/config": {
            **avail, "name": "Message", "unique_id": uid("message"), "icon": "mdi:message-text",
            "command_topic": f"{state}/set/send_text", "state_topic": state,
            "value_template": "{{ value_json.text | default('') }}", "max": 255,
        },
        f"{prefix}/select/{node}/preset/config": {
            **avail, "name": "Preset", "unique_id": uid("preset"), "icon": "mdi:palette",
            "command_topic": f"{state}/set/preset", "state_topic": state,
            "value_template": "{{ value_json.preset | default('') }}", "options": presets or ["—"],
        },
        f"{prefix}/switch/{node}/hold/config": {
            **avail, "name": "Hold connection", "unique_id": uid("hold"), "icon": "mdi:lock",
            "command_topic": f"{state}/set/hold", "state_topic": state,
            "value_template": "{{ value_json.hold | default('OFF') }}",
            "payload_on": "ON", "payload_off": "OFF", "entity_category": "config",
        },
        f"{prefix}/binary_sensor/{node}/connected/config": {
            **avail, "name": "Bluetooth", "unique_id": uid("connected"), "device_class": "connectivity",
            "state_topic": f"{state}/status", "payload_on": "online", "payload_off": "offline",
            "entity_category": "diagnostic",
        },
    }
    if rotations:
        out[f"{prefix}/select/{node}/rotation/config"] = {
            **avail, "name": "Rotation", "unique_id": uid("rotation"), "icon": "mdi:rotate-right",
            "command_topic": f"{state}/set/rotation", "state_topic": state,
            "value_template": "{{ value_json.rotation | default('" + NO_ROTATION + "') }}",
            "options": [NO_ROTATION, *rotations],
        }
    return out


def dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
