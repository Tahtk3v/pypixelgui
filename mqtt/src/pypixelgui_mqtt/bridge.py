"""MQTT bridge: lets home-automation systems drive the displays.

Topics (``<base>`` defaults to ``pypixel``, ``<alias>`` comes from the config):

    <base>/bridge/status            online | offline        (retained, LWT)
    <base>/<alias>/status           online | offline        (retained)
    <base>/<alias>/set/<command>    JSON object, or a bare value for the
                                    command's main parameter
    <base>/<alias>/set/hold         ON | OFF   (keep connection / release)
    <base>/<alias>/set/connect      (any)      connect now
    <base>/<alias>/set/disconnect   (any)
    <base>/<alias>/set/preset       preset name, e.g. "CYBER" or "Trans flag"
    <base>/<alias>/set/rotation     name of a rotation saved in the GUI; "stop" stops it
    <base>/<alias>/result           JSON {"command", "ok", "error"?}

zigbee2mqtt-style JSON (what Home Assistant uses; handy for Node-RED too):

    <base>/<alias>                  state JSON (retained): state, brightness (0-255),
                                    preset, text, rotation, hold, connected
    <base>/<alias>/set              JSON with any of: state ON|OFF|TOGGLE, brightness,
                                    effect|preset, text (+color, animation), rotation, hold

With Home Assistant discovery on (default), every saved display and display
group shows up in HA automatically – see ha.py. ``<alias>`` can also be a
display group's alias.

Examples::

    mosquitto_pub -t pypixel/desk/set/send_text -m 'Hello'
    mosquitto_pub -t pypixel/desk/set/send_text -m '{"text":"Hi","color":"ff0000","animation":"scroll_left"}'
    mosquitto_pub -t pypixel/desk/set/set_brightness -m 40

Security model: only devices listed in the config are addressable (by alias,
never by raw Bluetooth address); local-only commands (file paths, fonts) are
refused; ``clear``/``delete`` are refused unless ``allow_destructive`` is set.
Use broker authentication + TLS and broker ACLs on ``<base>/#``.
"""

from __future__ import annotations

import json
import logging
import ssl
from dataclasses import asdict, dataclass, fields
from typing import Any, Callable

from pypixelgui import groups, presets, rotation
from pypixelgui.commands import COMMANDS, CommandError
from pypixelgui.config import Settings
from pypixelgui.device_manager import DeviceManager

from . import ha

log = logging.getLogger(__name__)

_CONTROL = ("hold", "connect", "disconnect")


@dataclass
class MqttSettings:
    """Stored under ``plugins["mqtt"]`` in the shared pypixelgui config file."""
    enabled: bool = False
    host: str = "localhost"
    port: int = 1883
    username: str = ""
    password: str = ""
    tls: bool = False
    ca_certs: str = ""
    client_id: str = "pypixelgui"
    base_topic: str = "pypixel"
    #: Allow `clear` / `delete` over MQTT.
    allow_destructive: bool = False
    #: Publish Home Assistant MQTT discovery so displays appear in HA automatically.
    ha_discovery: bool = True
    ha_prefix: str = "homeassistant"

    @classmethod
    def from_settings(cls, settings: Settings) -> "MqttSettings":
        raw = settings.plugins.get("mqtt", {})
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in names})

    def store(self, settings: Settings) -> None:
        settings.plugins["mqtt"] = asdict(self)


def _error_of(fut) -> BaseException | None:
    if fut.cancelled():
        return RuntimeError("cancelled")
    return fut.exception()


def parse_payload(command: str, payload: bytes) -> dict[str, Any]:
    """Turn an MQTT payload into command parameters."""
    text = payload.decode("utf-8", errors="strict").strip() if payload else ""
    if not text:
        return {}
    try:
        value: Any = json.loads(text)
    except json.JSONDecodeError:
        value = text
    if isinstance(value, dict):
        return value
    spec = COMMANDS.get(command)
    if spec is None or spec.primary is None:
        raise CommandError(f"{command!r} needs a JSON object payload")
    return {spec.primary: value}


class MqttBridge:
    def __init__(self, manager: DeviceManager, settings: Settings,
                 on_status: Callable[[str], None] | None = None, client_factory=None):
        self.manager = manager
        self.settings = settings
        self.cfg = MqttSettings.from_settings(settings)
        self.on_status = on_status or (lambda s: None)
        self._client_factory = client_factory
        self._client = None
        self.connected = False
        self._state: dict[str, dict[str, Any]] = {}
        self._discovery_topics: set[str] = set()

    def _t(self, *parts: str) -> str:
        return "/".join([self.cfg.base_topic.strip("/"), *parts])

    # --- lifecycle --------------------------------------------------------

    def start(self) -> None:
        if self._client is not None:
            return
        if self._client_factory is not None:
            client = self._client_factory()
        else:
            try:
                import paho.mqtt.client as mqtt
            except ImportError as e:
                raise RuntimeError("MQTT support needs paho-mqtt: pip install pypixelgui-mqtt") from e
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.cfg.client_id)
        if self.cfg.username:
            client.username_pw_set(self.cfg.username, self.cfg.password or None)
        if self.cfg.tls:
            client.tls_set(ca_certs=self.cfg.ca_certs or None, cert_reqs=ssl.CERT_REQUIRED)
        client.will_set(self._t("bridge", "status"), "offline", qos=1, retain=True)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.reconnect_delay_set(1, 60)
        self._client = client
        self.manager.add_listener(self._on_device_event)
        self.on_status(f"Connecting to {self.cfg.host}:{self.cfg.port}…")
        client.connect_async(self.cfg.host, int(self.cfg.port), keepalive=60)
        client.loop_start()

    def stop(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        self.manager.remove_listener(self._on_device_event)
        try:
            client.publish(self._t("bridge", "status"), "offline", qos=1, retain=True).wait_for_publish(2)
        except Exception:
            pass
        client.disconnect()
        client.loop_stop()
        self.connected = False
        self.on_status("Stopped")

    # --- targets (saved displays and groups) -------------------------------------

    def targets(self) -> list[tuple[str, str, ha.Target]]:
        """(alias, address, HA description) for everything controllable over MQTT."""
        out = []
        for d in self.settings.devices:
            if d.kind == "iledcolor":
                p = d.panel or {}
                model = f"iLEDColor {p.get('width', '?')}×{p.get('height', '?')}"
                maker = "iLEDColor"
            else:
                model, maker = "iPixel Color", "iPixel"
            out.append((d.alias, d.address, ha.Target(d.alias, d.name or d.alias, model, maker)))
        for g in self.settings.groups:
            out.append((g["alias"], groups.address_of(g["alias"]),
                        ha.Target(g["alias"], g.get("name", g["alias"]),
                                  f"Group of {len(g.get('members', []))} displays ({g['cols']}×{g['rows']})")))
        return out

    def _resolve(self, alias: str) -> str | None:
        return next((addr for a, addr, _ in self.targets() if a == alias), None)

    def _alias_of(self, address: str) -> str | None:
        return next((a for a, addr, _ in self.targets() if addr == address), None)

    # --- paho callbacks (paho network thread) -----------------------------

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if getattr(reason_code, "is_failure", False) or (isinstance(reason_code, int) and reason_code):
            self.on_status(f"Broker refused connection: {reason_code}")
            return
        self.connected = True
        client.subscribe(self._t("+", "set", "+"), qos=1)
        client.subscribe(self._t("+", "set"), qos=1)
        if self.cfg.ha_discovery:
            client.subscribe(f"{self.cfg.ha_prefix}/status", qos=1)  # HA's birth message
        client.publish(self._t("bridge", "status"), "online", qos=1, retain=True)
        self.publish_all()
        self.on_status(f"Connected to {self.cfg.host}:{self.cfg.port}")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self.connected = False
        if self._client is not None:
            self.on_status(f"Disconnected from broker ({reason_code}); retrying…")

    def _on_message(self, client, userdata, msg):
        if self.cfg.ha_discovery and msg.topic == f"{self.cfg.ha_prefix}/status":
            if msg.payload.decode(errors="ignore").strip().lower() == "online":
                self.publish_all()  # HA restarted: send discovery and state again
            return
        parts = msg.topic.split("/")
        base = self.cfg.base_topic.strip("/").split("/")
        if parts[:len(base)] != base:
            return
        rest = parts[len(base):]
        if len(rest) == 3 and rest[1] == "set":
            self.handle(rest[0], rest[2], msg.payload)
        elif len(rest) == 2 and rest[1] == "set":
            self.handle_json(rest[0], msg.payload)

    # --- publishing --------------------------------------------------------------------

    def publish_all(self) -> None:
        """Discovery (if enabled), state and status for every target. Call after settings change."""
        if self._client is None:
            return
        for alias, address, _ in self.targets():
            self._publish_status(address)
            self._publish_state(alias, address)
        if self.cfg.ha_discovery:
            self.publish_discovery()

    def publish_discovery(self) -> None:
        names = [p.name for p in presets.all_presets(self.settings.presets)]
        rotations = [r["name"] for r in self.settings.rotations]
        wanted: dict[str, dict] = {}
        for _alias, _addr, target in self.targets():
            wanted.update(ha.configs(target, self.cfg.base_topic.strip("/"), self.cfg.ha_prefix, names, rotations))
        for topic in self._discovery_topics - set(wanted):  # removed displays/entities
            self._client.publish(topic, "", qos=1, retain=True)
        for topic, payload in wanted.items():
            self._client.publish(topic, ha.dumps(payload), qos=1, retain=True)
        self._discovery_topics = set(wanted)

    def _state_of(self, alias: str, address: str) -> dict[str, Any]:
        st = self._state.setdefault(alias, {"state": "ON", "brightness": 255, "preset": "", "text": ""})
        st["hold"] = "ON" if self.manager.is_held(address) else "OFF"
        st["connected"] = self.manager.is_connected(address)
        st["rotation"] = self.manager.rotation_name(address) or ha.NO_ROTATION
        return st

    def _publish_state(self, alias: str, address: str) -> None:
        if self._client is not None and self.connected:
            self._client.publish(self._t(alias), json.dumps(self._state_of(alias, address)), qos=0, retain=True)

    def _publish_status(self, address: str) -> None:
        alias = self._alias_of(address)
        if alias is None or self._client is None:
            return
        state = "online" if self.manager.is_connected(address) else "offline"
        self._client.publish(self._t(alias, "status"), state, qos=1, retain=True)

    def _publish_result(self, alias: str, command: str, error: BaseException | None) -> None:
        body: dict[str, Any] = {"command": command, "ok": error is None}
        if error is not None:
            body["error"] = str(error)
        if self._client is not None:
            self._client.publish(self._t(alias, "result"), json.dumps(body), qos=0)

    def _on_device_event(self, event: str, data: dict) -> None:
        address = data.get("address")
        alias = self._alias_of(address) if address else None
        if alias is None or not self.connected:
            return
        st = self._state_of(alias, address)
        if event == "command_ok":
            params = data.get("params") or {}
            if data["command"] == "set_power" and "on" in params:
                st["state"] = "ON" if params["on"] else "OFF"
            elif data["command"] == "set_brightness" and "level" in params:
                st["brightness"] = round(int(params["level"]) * 255 / 100)
            elif data["command"] == "send_text":
                st["text"], st["preset"] = params.get("text", ""), ""
            elif data["command"] == "send_image":
                st["text"], st["preset"] = "", ""
        elif event == "preset_ok":
            st["preset"], st["text"] = data.get("preset", ""), ""
        elif event in ("connected", "disconnected"):
            self._publish_status(address)
        elif event not in ("hold_changed", "rotation_started", "rotation_stopped", "reconnected"):
            return
        self._publish_state(alias, address)

    # --- request handling ----------------------------------------------------------

    def _opts(self) -> dict:
        return {"remote": True, "allow_destructive": self.cfg.allow_destructive, "connect": True}

    def _done(self, alias: str, command: str, fut) -> None:
        fut.add_done_callback(lambda f: self._publish_result(alias, command, _error_of(f)))

    def handle(self, alias: str, command: str, payload: bytes) -> None:
        address = self._resolve(alias)
        if address is None:
            log.warning("MQTT: ignoring command for unknown alias %r", alias)
            return
        try:
            if command in _CONTROL:
                fut = self._control(address, command, payload)
            elif command == "preset":
                fut = self._preset(address, payload.decode("utf-8").strip())
            elif command == "rotation":
                self._rotation(alias, address, payload.decode("utf-8").strip())
                return
            else:
                params = parse_payload(command, payload)
                self.manager.stop_rotation(address)  # a direct command takes over the display
                fut = self.manager.run(address, command, params, **self._opts())
        except (CommandError, UnicodeDecodeError, ValueError) as e:
            self._publish_result(alias, command, e)
            return
        self._done(alias, command, fut)

    def handle_json(self, alias: str, payload: bytes) -> None:
        """zigbee2mqtt-style ``<base>/<alias>/set`` with a JSON object."""
        address = self._resolve(alias)
        if address is None:
            log.warning("MQTT: ignoring command for unknown alias %r", alias)
            return
        try:
            req = json.loads(payload.decode("utf-8") or "{}")
            if not isinstance(req, dict):
                raise CommandError("expected a JSON object")
            st = self._state_of(alias, address)
            state = str(req.get("state", "")).upper()
            if state == "TOGGLE":
                state = "OFF" if st.get("state") == "ON" else "ON"
            if "brightness" in req:
                level = max(0, min(100, round(float(req["brightness"]) * 100 / 255)))
                self._done(alias, "set_brightness",
                           self.manager.run(address, "set_brightness", {"level": level}, **self._opts()))
            if state in ("ON", "OFF"):
                self._done(alias, "set_power",
                           self.manager.run(address, "set_power", {"on": state == "ON"}, **self._opts()))
            name = req.get("effect") or req.get("preset")
            if name:
                self._done(alias, "preset", self._preset(address, str(name)))
            if req.get("text"):
                params = {"text": req["text"]}
                for k in ("color", "bg_color", "animation", "speed"):
                    if k in req:
                        params[k] = req[k]
                self.manager.stop_rotation(address)
                self._done(alias, "send_text", self.manager.run(address, "send_text", params, **self._opts()))
            if "rotation" in req:
                self._rotation(alias, address, str(req["rotation"]))
            if "hold" in req:
                self._done(alias, "hold", self._control(address, "hold", str(req["hold"]).encode()))
        except (CommandError, UnicodeDecodeError, ValueError) as e:
            self._publish_result(alias, "set", e)

    def _preset(self, address: str, name: str):
        preset = presets.find(name, self.settings.presets)
        if preset is None:
            raise CommandError(f"unknown preset {name!r}")
        self.manager.stop_rotation(address)
        return self.manager.run_preset(address, preset, **self._opts())

    def _rotation(self, alias: str, address: str, name: str) -> None:
        if name.lower() in ("", "stop", "off", ha.NO_ROTATION):
            self.manager.stop_rotation(address)
            self._publish_result(alias, "rotation", None)
            self._publish_state(alias, address)
            return
        entry = rotation.find(name, self.settings.rotations)
        if entry is None:
            raise CommandError(f"unknown rotation {name!r}")
        try:
            items = rotation.resolve(entry, self.settings.presets)
        except (KeyError, ValueError) as e:
            raise CommandError(str(e)) from None
        # A rotation runs until stopped, so report that it started rather than wait for it.
        self.manager.start_rotation(address, items, loop=entry.get("loop", True),
                                    preload_slot=entry.get("preload_slot"), name=entry["name"], **self._opts())
        self._publish_result(alias, "rotation", None)

    def _control(self, address: str, command: str, payload: bytes):
        if command == "connect":
            return self.manager.connect(address)
        if command == "disconnect":
            return self.manager.disconnect(address)
        on = payload.decode().strip().lower() in ("1", "on", "true", "yes")
        return self.manager.set_hold(address, on)
