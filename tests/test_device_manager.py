import asyncio
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


class _SessionClient(FakeClient):
    """FakeClient whose session runs plans the way pypixelcolor's DeviceSession does."""

    def __init__(self, address):
        super().__init__(address)
        session = self._session
        sent = self.sent_windows = []

        async def execute_command(command_func, *args, **kwargs):
            import inspect
            assert "device_info" not in inspect.signature(command_func).parameters
            plan = command_func(*args, **kwargs)
            for w in plan.windows:
                sent.append(w)
            return type("R", (), {"success": True})()
        session.execute_command = execute_command


def test_send_image_file_uploads_exact_bytes_with_progress(tmp_path):
    pytest.importorskip("pypixelcolor")
    from PIL import Image
    path = tmp_path / "a.gif"
    frames = [Image.new("RGB", (120, 90), c) for c in ("red", "blue", "green")]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=100, loop=0)

    m = DeviceManager(client_factory=_SessionClient)
    events = []
    m.add_listener(lambda ev, data: events.append((ev, data)))
    try:
        m.connect("AA").result(2)
        prepared = m.send_image_file("AA", str(path), budget_kb=50).result(10)
    finally:
        m.shutdown()
    client = FakeClient.instances[-1]
    assert prepared.ext == ".gif" and prepared.width == 32 and prepared.height == 32
    assert client.sent_windows and client.calls == []  # went through the session, not re-encoded
    payload = b"".join(w.data for w in client.sent_windows)
    assert prepared.data[:200] in payload  # our bytes, not a re-encoded copy
    progress = [d for ev, d in events if ev == "upload_progress"]
    assert progress and progress[-1]["sent"] == progress[-1]["total"]
    assert any(ev == "upload_finished" and d["ok"] for ev, d in events)


def test_image_preset_and_capabilities_with_iledcolor(tmp_path, monkeypatch):
    """Saved image preset → prepared → driver upload; unsupported commands are refused."""
    from PIL import Image
    from pypixelgui import presets
    from pypixelgui.drivers.iledcolor import ILedColorClient
    from pypixelgui.drivers.iledcolor_protocol import PanelInfo
    monkeypatch.setattr("pypixelgui.config.media_dir", lambda: tmp_path)
    Image.new("RGB", (100, 40), "blue").save(tmp_path / "abc.png")

    uploads = []

    class Driver(ILedColorClient):
        async def connect(self):
            self.is_connected = True

        async def send_prepared(self, prepared, save_slot=0, progress=None):
            uploads.append(prepared)
            if progress:
                progress(1, 1, 10, 10)

    m = DeviceManager(client_factory=lambda a: Driver(a, PanelInfo(48, 12, 1)))
    try:
        m.connect("AA").result(2)
        entry = {"name": "Mine", "steps": [["image_file", {"file": "abc.png"}]]}
        m.run_preset("AA", presets.user_preset(entry)).result(10)
        assert uploads and (uploads[0].width, uploads[0].height) == (48, 12)
        with pytest.raises(RuntimeError, match="doesn't support"):
            m.run("AA", "set_clock_mode", {"style": 1}).result(2)
        assert m.capabilities("AA") is not None and "send_text" in m.capabilities("AA")
    finally:
        m.shutdown()


def test_scan_remembers_kind_and_panel():
    from pypixelgui.device_manager import FoundDevice, classify

    assert classify("iledcolor-23D4", {21505: bytes.fromhex("424402001000400300000900020094000000")})[0] \
        == "iledcolor"
    assert classify("LED_BLE_1", {}) == ("ipixel", None)
    assert classify("Headphones", {}) is None

    async def scan(_t):
        return [FoundDevice("iledcolor-1", "BB", kind="iledcolor", panel={"width": 64, "height": 16,
                                                                          "color_type": 3})]
    m = DeviceManager(scan_func=scan)
    try:
        m.scan(1).result(2)
        assert m.kind("BB") == "iledcolor"
    finally:
        m.shutdown()


def test_live_preset_reuploads_only_when_content_changes(manager):
    from pypixelgui.presets import Preset
    manager.connect("AA").result(2)
    text = {"value": "12 3'"}
    live = Preset("Bus", "My presets", "", lambda w, h: [("send_text", {"text": text["value"]})], refresh=0.5)
    manager.show_preset("AA", live)
    client = FakeClient.instances[-1]
    assert _wait(lambda: len(client.calls) == 1)
    time.sleep(1.2)                       # two re-checks, nothing changed
    assert len(client.calls) == 1 and "live_unchanged" in manager.events
    text["value"] = "12 2'"               # the departure is now a minute closer
    assert _wait(lambda: len(client.calls) == 2)
    assert client.calls[-1] == ("send_text", {"text": "12 2'"})
    # sending anything else by hand stops the updating
    manager.stop_rotation("AA")
    manager.run("AA", "set_brightness", {"level": 10}).result(2)
    text["value"] = "12 1'"
    time.sleep(1.0)
    assert client.calls[-1] == ("set_brightness", {"level": 10})


def test_static_preset_is_shown_once(manager):
    from pypixelgui import presets
    manager.connect("AA").result(2)
    manager.show_preset("AA", presets.find("ON AIR")).result(2)
    assert not manager.rotation_running("AA")


def test_live_presets_have_sensible_refresh():
    from pypixelgui import presets
    weather = presets.user_preset({"name": "w", "steps": [["weather", {"latitude": 1, "longitude": 2}]]})
    bus = presets.user_preset({"name": "b", "steps": [["transit", {"stop": "x"}]]})
    custom = presets.user_preset({"name": "c", "steps": [["schedule", {"url": "u", "refresh": 300}]]})
    assert (weather.refresh, bus.refresh, custom.refresh) == (600, 60, 300)


class _SlowUpload(FakeClient):
    """An upload that takes a while, like ~100 KB over Bluetooth."""
    connects = 0

    async def connect(self):
        _SlowUpload.connects += 1
        await super().connect()

    async def send_image_hex(self, **kw):
        self.calls.append(("upload", kw.get("file_extension")))
        await asyncio.sleep(5)


def _gif_b64(size=(32, 32)):
    import base64
    import io
    from PIL import Image
    frames = [Image.new("RGB", size, c) for c in ("red", "blue")]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    return base64.b64encode(buf.getvalue()).decode()


def test_interrupted_upload_resets_the_display_before_the_next_command():
    import asyncio as _a  # noqa: F401
    _SlowUpload.connects = 0
    m = DeviceManager(client_factory=_SlowUpload)
    events = []
    m.add_listener(lambda ev, d: events.append(ev))
    try:
        m.connect("AA").result(2)
        from pypixelgui.presets import Preset
        data = _gif_b64()
        live = Preset("Schedule", "My", "", lambda w, h: [("send_image_b64", {"data": data, "ext": ".gif"})],
                      refresh=120)
        m.show_preset("AA", live)
        assert _wait(lambda: "upload_prepared" in events)  # upload under way, with progress events
        m.stop_rotation("AA")                                # user sends something else
        t = time.monotonic()
        m.run("AA", "set_brightness", {"level": 20}).result(5)
        assert time.monotonic() - t < 2                      # no waiting for the old upload
        assert "resetting" in events and _SlowUpload.connects == 2  # reconnected to reset the display
        assert FakeClient.instances[-1].calls[-1] == ("set_brightness", {"level": 20})
    finally:
        m.shutdown()


def test_disconnect_stops_live_updates_and_cannot_hang():
    class Stuck(FakeClient):
        async def disconnect(self):
            await asyncio.sleep(60)  # a display stuck mid-transfer that never answers

    m = DeviceManager(client_factory=Stuck)
    try:
        m.connect("AA").result(2)
        from pypixelgui.presets import Preset
        live = Preset("x", "My", "", lambda w, h: [("send_text", {"text": "hi"})], refresh=60)
        m.show_preset("AA", live)
        assert _wait(lambda: m.rotation_running("AA"))
        t = time.monotonic()
        m.disconnect("AA").result(10)
        assert time.monotonic() - t < 7
        assert not m.rotation_running("AA") and not m.is_connected("AA")
    finally:
        m.shutdown()
