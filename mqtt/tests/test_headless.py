import json

import pytest

from pypixelgui.config import DeviceEntry, Settings
from pypixelgui.device_manager import FoundDevice
from pypixelgui_mqtt import headless
from pypixelgui_mqtt.bridge import MqttSettings


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / "config.json"

    class FakeManager:  # scan without Bluetooth
        def __init__(self, *a, **k):
            pass

        def scan(self, seconds):
            class F:
                def result(self, timeout=None):
                    return [FoundDevice("iledcolor-23D4", "AA:BB", kind="iledcolor",
                                        panel={"width": 64, "height": 16, "color_type": 3}),
                            FoundDevice("LED_BLE_1", "CC:DD")]
            return F()

        def shutdown(self):
            pass
    monkeypatch.setattr("pypixelgui.device_manager.DeviceManager", FakeManager)
    return path


def run(path, *argv):
    return headless.main(["--config", str(path), *argv])


def test_setup_from_a_terminal(cfg, capsys):
    assert run(cfg, "scan") == 0
    assert "iledcolor-23D4" in capsys.readouterr().out
    assert run(cfg, "add", "AA:BB", "--name", "Window") == 0
    assert run(cfg, "add", "CC:DD") == 0
    assert run(cfg, "mqtt", "--host", "homeassistant.local", "--username", "mqtt", "--password", "pw",
               "--no-ha-discovery") == 0
    s = Settings.load(cfg)
    window = s.find("AA:BB")
    assert window.kind == "iledcolor" and window.panel["width"] == 64 and window.hold
    assert s.find("CC:DD").kind == "ipixel"
    m = MqttSettings.from_settings(s)
    assert (m.host, m.username, m.ha_discovery, m.enabled) == ("homeassistant.local", "mqtt", False, True)
    assert run(cfg, "list") == 0
    assert "window" in capsys.readouterr().out
    assert run(cfg, "remove", "window") == 0
    assert Settings.load(cfg).find("AA:BB") is None


def test_iledcolor_needs_to_be_seen(cfg, capsys):
    assert run(cfg, "add", "EE:FF", "--kind", "iledcolor") == 1


def test_run_without_displays_explains(cfg, capsys):
    assert run(cfg) == 2
    assert "scan" in capsys.readouterr().err


def test_env_overrides_for_containers():
    cfg = headless._env_overrides(MqttSettings(), {
        "PYPIXELGUI_MQTT_HOST": "core-mosquitto", "PYPIXELGUI_MQTT_PORT": "1884",
        "PYPIXELGUI_MQTT_TLS": "1", "PYPIXELGUI_MQTT_HA_DISCOVERY": "0", "PYPIXELGUI_MQTT_USERNAME": ""})
    assert (cfg.host, cfg.port, cfg.tls, cfg.ha_discovery, cfg.username) == ("core-mosquitto", 1884, True, False, "")
