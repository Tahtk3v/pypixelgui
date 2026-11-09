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
    <base>/<alias>/result           JSON {"command", "ok", "error"?}

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

from pypixelgui import presets
from pypixelgui.commands import COMMANDS, CommandError
from pypixelgui.config import Settings
from pypixelgui.device_manager import DeviceManager

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

    # --- paho callbacks (paho network thread) -----------------------------

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if getattr(reason_code, "is_failure", False) or (isinstance(reason_code, int) and reason_code):
            self.on_status(f"Broker refused connection: {reason_code}")
            return
        self.connected = True
        client.subscribe(self._t("+", "set", "+"), qos=1)
        client.publish(self._t("bridge", "status"), "online", qos=1, retain=True)
        for dev in self.settings.devices:
            self._publish_status(dev.address)
        self.on_status(f"Connected to {self.cfg.host}:{self.cfg.port}")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self.connected = False
        if self._client is not None:
            self.on_status(f"Disconnected from broker ({reason_code}); retrying…")

    def _on_message(self, client, userdata, msg):
        parts = msg.topic.split("/")
        base = self.cfg.base_topic.strip("/").split("/")
        if parts[:len(base)] != base or len(parts) != len(base) + 3:
            return
        alias, _, command = parts[len(base):]
        self.handle(alias, command, msg.payload)

    # --- request handling -------------------------------------------------

    def handle(self, alias: str, command: str, payload: bytes) -> None:
        dev = self.settings.by_alias(alias)
        if dev is None:
            log.warning("MQTT: ignoring command for unknown alias %r", alias)
            return
        try:
            if command in _CONTROL:
                fut = self._control(dev.address, command, payload)
            elif command == "preset":
                name = payload.decode("utf-8").strip()
                preset = presets.find(name, self.settings.presets)
                if preset is None:
                    raise CommandError(f"unknown preset {name!r}")
                fut = self.manager.run_preset(dev.address, preset, remote=True,
                                              allow_destructive=self.cfg.allow_destructive)
            else:
                params = parse_payload(command, payload)
                fut = self.manager.run(dev.address, command, params, remote=True,
                                       allow_destructive=self.cfg.allow_destructive)
        except (CommandError, UnicodeDecodeError) as e:
            self._publish_result(alias, command, e)
            return
        fut.add_done_callback(lambda f: self._publish_result(alias, command, _error_of(f)))

    def _control(self, address: str, command: str, payload: bytes):
        if command == "connect":
            return self.manager.connect(address)
        if command == "disconnect":
            return self.manager.disconnect(address)
        on = payload.decode().strip().lower() in ("1", "on", "true", "yes")
        return self.manager.set_hold(address, on)

    def _publish_result(self, alias: str, command: str, error: BaseException | None) -> None:
        body: dict[str, Any] = {"command": command, "ok": error is None}
        if error is not None:
            body["error"] = str(error)
        if self._client is not None:
            self._client.publish(self._t(alias, "result"), json.dumps(body), qos=0)

    def _publish_status(self, address: str) -> None:
        dev = self.settings.find(address)
        if dev is None or self._client is None:
            return
        state = "online" if self.manager.is_connected(address) else "offline"
        self._client.publish(self._t(dev.alias, "status"), state, qos=1, retain=True)

    def _on_device_event(self, event: str, data: dict) -> None:
        if event in ("connected", "disconnected") and self.connected:
            self._publish_status(data["address"])
