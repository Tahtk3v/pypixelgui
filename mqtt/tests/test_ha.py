import json
import time
from types import SimpleNamespace

import pytest

from pypixelgui import groups
from pypixelgui.config import DeviceEntry, Settings
from pypixelgui.device_manager import DeviceManager
from pypixelgui_mqtt import ha
from pypixelgui_mqtt.bridge import MqttBridge
from fakes import FakeClient
from test_bridge import FakeMqtt


def _wait(cond, timeout=3):
    deadline = time.time() + timeout
    while time.time() < deadline and not cond():
        time.sleep(0.02)
    return cond()


@pytest.fixture
def env():
    FakeClient.instances.clear()
    settings = Settings(devices=[DeviceEntry(address="AA", name="Desk"),
                                 DeviceEntry(address="BB", name="Window", kind="iledcolor",
                                             panel={"width": 64, "height": 16, "color_type": 3})],
                        rotations=[{"name": "Shop", "items": [{"preset": "ON AIR", "seconds": 1}]}],
                        groups=[{"name": "Wall", "alias": "wall", "cols": 2, "rows": 1, "members": ["AA", "CC"]}])
    manager = DeviceManager(client_factory=FakeClient)
    mqtt = FakeMqtt()
    mqtt.subscriptions = []
    mqtt.subscribe = lambda topic, qos=0: mqtt.subscriptions.append(topic)
    bridge = MqttBridge(manager, settings, client_factory=lambda: mqtt)
    bridge.start()
    bridge._on_connect(mqtt, None, None, 0)
    yield bridge, mqtt, manager
    bridge.stop()
    manager.shutdown()


def _configs(mqtt):
    return {t: json.loads(p) for t, p in mqtt.published if t.startswith("homeassistant/") and p}


def test_discovery_for_every_display_and_group(env):
    bridge, mqtt, _ = env
    cfg = _configs(mqtt)
    lights = {t: c for t, c in cfg.items() if "/light/" in t}
    assert set(lights) == {f"homeassistant/light/pypixelgui_{a}/light/config" for a in ("desk", "window", "wall")}
    desk = lights["homeassistant/light/pypixelgui_desk/light/config"]
    assert desk["command_topic"] == "pypixel/desk/set" and desk["schema"] == "json"
    assert "CYBER" in desk["effect_list"] and desk["availability"][0]["topic"] == "pypixel/bridge/status"
    assert cfg["homeassistant/select/pypixelgui_desk/rotation/config"]["options"] == [ha.NO_ROTATION, "Shop"]
    assert "iLEDColor 64×16" in json.dumps(cfg, ensure_ascii=False)
    assert "homeassistant/status" in mqtt.subscriptions and "pypixel/+/set" in mqtt.subscriptions
    # unique ids are unique
    uids = [c["unique_id"] for c in cfg.values()]
    assert len(uids) == len(set(uids))


def test_removed_display_clears_its_discovery(env):
    bridge, mqtt, _ = env
    bridge.settings.devices = [d for d in bridge.settings.devices if d.alias != "window"]
    mqtt.published.clear()
    bridge.publish_all()
    cleared = [t for t, p in mqtt.published if p == "" and "pypixelgui_window" in t]
    assert "homeassistant/light/pypixelgui_window/light/config" in cleared


def test_ha_birth_republishes(env):
    bridge, mqtt, _ = env
    mqtt.published.clear()
    bridge._on_message(None, None, SimpleNamespace(topic="homeassistant/status", payload=b"online"))
    assert _configs(mqtt)


def test_json_set_light_and_auto_connect(env):
    bridge, mqtt, manager = env
    assert not manager.is_connected("AA")
    bridge._on_message(None, None, SimpleNamespace(topic="pypixel/desk/set",
                                                   payload=b'{"state":"ON","brightness":128,"effect":"ON AIR"}'))
    assert _wait(lambda: len(FakeClient.instances) == 1 and len(FakeClient.instances[0].calls) == 3)
    calls = FakeClient.instances[0].calls
    assert calls[0] == ("set_brightness", {"level": 50})
    assert calls[1] == ("set_power", {"on": True})
    assert calls[2][0] == "send_text" and calls[2][1]["text"] == "ON AIR"
    assert _wait(lambda: json.loads([p for t, p in mqtt.published if t == "pypixel/desk"][-1])["preset"] == "ON AIR")
    state = json.loads([p for t, p in mqtt.published if t == "pypixel/desk"][-1])
    assert state["brightness"] == 128 and state["state"] == "ON" and state["connected"] is True


def test_text_and_rotation_select(env):
    bridge, mqtt, manager = env
    bridge.handle("desk", "send_text", b"Hallo")
    assert _wait(lambda: FakeClient.instances and FakeClient.instances[0].calls)
    bridge.handle("desk", "rotation", b"Shop")
    assert _wait(lambda: manager.rotation_running("AA"))
    assert manager.rotation_name("AA") == "Shop"
    bridge.handle("desk", "rotation", ha.NO_ROTATION.encode())
    assert _wait(lambda: not manager.rotation_running("AA"))


def test_group_alias_is_addressable(env):
    bridge, _, _ = env
    assert bridge._resolve("wall") == groups.address_of("wall")
