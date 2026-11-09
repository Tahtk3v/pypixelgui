import time

import pytest

from pypixelgui.commands import CommandError
from pypixelgui.device_manager import DeviceManager
from fakes import FakeClient


@pytest.fixture
def manager():
    FakeClient.instances.clear()
    FakeClient.fail_connect = False
    m = DeviceManager(client_factory=FakeClient, watchdog_interval=0.05)
    events = []
    m.add_listener(lambda ev, data: events.append(ev))
    m.events = events
    yield m
    m.shutdown()


def test_connect_and_run(manager):
    manager.connect("AA").result(2)
    manager.run("AA", "set_brightness", {"level": "30"}).result(2)
    assert FakeClient.instances[-1].calls == [("set_brightness", {"level": 30})]
    assert "connected" in manager.events and "command_ok" in manager.events


def test_run_requires_connection(manager):
    with pytest.raises(RuntimeError):
        manager.run("AA", "set_power", {"on": True}).result(2)


def test_validation_errors_propagate(manager):
    manager.connect("AA").result(2)
    with pytest.raises(CommandError):
        manager.run("AA", "set_brightness", {"level": 500}).result(2)


def test_hold_reconnects_after_drop(manager):
    manager.set_hold("AA", True).result(2)
    first = FakeClient.instances[-1]
    first._session.is_connected = False  # simulate BLE drop
    deadline = time.time() + 2
    while time.time() < deadline and (len(FakeClient.instances) < 2 or not manager.is_connected("AA")):
        time.sleep(0.02)
    assert len(FakeClient.instances) >= 2 and manager.is_connected("AA")
    assert "reconnected" in manager.events


def test_disconnect_releases_hold(manager):
    manager.set_hold("AA", True).result(2)
    manager.disconnect("AA").result(2)
    assert not manager.is_held("AA") and not manager.is_connected("AA")


def test_presets_render_for_display(manager):
    from pypixelgui import presets
    manager.connect("AA").result(2)
    manager.run_preset("AA", presets.find("ON AIR")).result(2)
    manager.run_preset("AA", presets.find("trans flag")).result(2)
    manager.run_preset("AA", presets.find("CYBER")).result(2)
    calls = FakeClient.instances[-1].calls
    assert calls[0] == ("send_text", {"text": "ON AIR", "color": "ff2020", "animation": "static",
                                      "speed": 80, "rainbow_mode": 0})
    assert calls[1][0] == "send_image_hex" and calls[1][1]["file_extension"] == ".png"
    assert calls[2][0] == "send_image_hex" and calls[2][1]["file_extension"] == ".gif"
    assert "preset_ok" in manager.events


def _wait(cond, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline and not cond():
        time.sleep(0.02)
    return cond()


def test_rotation_runs_in_order_and_stops(manager):
    from pypixelgui import presets
    manager.connect("AA").result(2)
    items = [(presets.find("ON AIR"), 0.5), (presets.find("Trans flag"), 0.5)]
    fut = manager.start_rotation("AA", items, loop=False)
    fut.result(5)
    calls = [c for c, _ in FakeClient.instances[-1].calls]
    assert calls == ["send_text", "send_image_hex"]
    assert not manager.rotation_running("AA")
    assert "rotation_step" in manager.events and "rotation_stopped" in manager.events


def test_rotation_loops_until_stopped(manager):
    from pypixelgui import presets
    manager.connect("AA").result(2)
    manager.start_rotation("AA", [(presets.find("ON AIR"), 0.5)], loop=True)
    assert _wait(lambda: len(FakeClient.instances[-1].calls) >= 2)
    assert manager.stop_rotation("AA")
    assert _wait(lambda: not manager.rotation_running("AA"))


def test_rotation_preload_uses_slots(manager):
    from pypixelgui import presets
    manager.connect("AA").result(2)
    items = [(presets.find("CYBER"), 0.5), (presets.find("ON AIR"), 0.5)]
    manager.start_rotation("AA", items, loop=False, preload_slot=3).result(5)
    calls = FakeClient.instances[-1].calls
    assert calls[0][0] == "send_image_hex" and calls[0][1]["save_slot"] == 3
    assert calls[1] == ("send_text", {**calls[1][1], "save_slot": 4})
    assert calls[2:] == [("show_slot", {"number": 3}), ("show_slot", {"number": 4})]
