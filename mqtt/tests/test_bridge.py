import json
import time
from types import SimpleNamespace

import pytest

from pypixelgui.commands import CommandError
from pypixelgui.config import DeviceEntry, Settings
from pypixelgui.device_manager import DeviceManager
from pypixelgui_mqtt.bridge import MqttBridge, MqttSettings, parse_payload
from fakes import FakeClient


class FakeMqtt:
    def __init__(self):
        self.published = []
        self.on_connect = self.on_disconnect = self.on_message = None

    def __getattr__(self, name):  # username_pw_set, will_set, loop_start, ...
        return lambda *a, **k: None

    def publish(self, topic, payload=None, qos=0, retain=False):
        self.published.append((topic, payload))
        return SimpleNamespace(wait_for_publish=lambda *_: None)

    def subscribe(self, *a, **k):
        pass


@pytest.fixture
def setup():
    FakeClient.instances.clear()
    settings = Settings(devices=[DeviceEntry(address="AA:BB", name="Desk Sign")])
    manager = DeviceManager(client_factory=FakeClient)
    manager.connect("AA:BB").result(2)
    mqtt = FakeMqtt()
    bridge = MqttBridge(manager, settings, client_factory=lambda: mqtt)
    bridge.start()
    yield bridge, mqtt
    bridge.stop()
    manager.shutdown()


def _results(mqtt):
    deadline = time.time() + 2
    while time.time() < deadline and not any(t.endswith("/result") for t, _ in mqtt.published):
        time.sleep(0.01)
    return [json.loads(p) for t, p in mqtt.published if t.endswith("/result")]


def test_parse_payload():
    assert parse_payload("send_text", b"hello") == {"text": "hello"}
    assert parse_payload("set_brightness", b"40") == {"level": 40}
    assert parse_payload("send_text", b'{"text":"a","color":"ff0000"}') == {"text": "a", "color": "ff0000"}
    assert parse_payload("clear", b"") == {}
    with pytest.raises(CommandError):
        parse_payload("set_pixel", b"3")


def test_alias_and_command(setup):
    bridge, mqtt = setup
    assert bridge.settings.devices[0].alias == "desk_sign"
    bridge._on_message(None, None, SimpleNamespace(topic="pypixel/desk_sign/set/send_text", payload=b"Hi"))
    assert _results(mqtt) == [{"command": "send_text", "ok": True}]
    assert FakeClient.instances[-1].calls == [("send_text", {"text": "Hi"})]


def test_unknown_alias_ignored(setup):
    bridge, mqtt = setup
    bridge._on_message(None, None, SimpleNamespace(topic="pypixel/AA:BB/set/clear", payload=b""))
    time.sleep(0.05)
    assert FakeClient.instances[-1].calls == []


def test_destructive_blocked_by_default(setup):
    bridge, mqtt = setup
    bridge.handle("desk_sign", "clear", b"")
    res = _results(mqtt)
    assert res[0]["ok"] is False and "destructive" in res[0]["error"]


def test_preset_over_mqtt(setup):
    bridge, mqtt = setup
    bridge.handle("desk_sign", "preset", b"CYBER")
    assert _results(mqtt) == [{"command": "preset", "ok": True}]
    assert FakeClient.instances[-1].calls[0][1]["text"] == "CYBER"


def test_settings_live_in_plugin_section():
    s = Settings()
    MqttSettings(host="broker.lan", port=8883, tls=True).store(s)
    assert s.plugins["mqtt"]["host"] == "broker.lan"
    assert MqttSettings.from_settings(s).port == 8883
