import io
import time

import pytest
from PIL import Image, ImageSequence

from pypixelgui import groups, media, presets
from pypixelgui.device_manager import DeviceManager
from pypixelgui.drivers.iledcolor import ILedColorClient
from pypixelgui.drivers.iledcolor_protocol import PanelInfo


class Panel(ILedColorClient):
    """iLEDColor client with Bluetooth replaced by a record of what it was sent."""
    log: list = []

    async def connect(self):
        self.is_connected = True

    async def send_prepared(self, prepared, save_slot=0, progress=None):
        Panel.log.append(("upload", self.address, prepared))
        self._last_program = (1, 1)

    async def replay_last(self):
        Panel.log.append(("replay", self.address, time.monotonic()))

    async def set_brightness(self, level):
        Panel.log.append(("brightness", self.address, level))


@pytest.fixture
def wall():
    Panel.log = []
    m = DeviceManager(client_factory=None)
    m._client_factory = lambda a: m._make_client(a) if groups.is_group(a) else Panel(a, PanelInfo(32, 16, 3))
    spec = groups.GroupSpec("Wall", "wall", cols=2, rows=1, members=["L", "R"])
    m.set_group(spec)
    yield m, spec
    m.shutdown()


def test_split_cuts_tiles_and_keeps_timing():
    img = Image.new("RGB", (64, 16))
    img.paste((255, 0, 0), (0, 0, 32, 16))
    img.paste((0, 0, 255), (32, 0, 64, 16))
    frames = [img, img.transpose(Image.FLIP_LEFT_RIGHT), img]
    p = media.prepare_frames(frames, [100, 200, 300])
    left, right = media.split(p, 2, 1)
    lf = [f.convert("RGB") for f in ImageSequence.Iterator(Image.open(io.BytesIO(left.data)))]
    rf = [f.convert("RGB") for f in ImageSequence.Iterator(Image.open(io.BytesIO(right.data)))]
    assert left.width == 32 and len(lf) == len(rf)
    assert lf[0].getpixel((0, 0)) == (255, 0, 0) and rf[0].getpixel((0, 0)) == (0, 0, 255)
    assert lf[1].getpixel((0, 0)) == (0, 0, 255)


def test_group_is_one_wide_display(wall):
    m, spec = wall
    m.connect(spec.address).result(5)
    info = m.device_info(spec.address)
    assert (info.width, info.height, info.panels) == (64, 16, 2)
    assert m.is_connected("L") and m.is_connected("R")  # members connected via the group

    m.run(spec.address, "send_text", {"text": "Hello wide world", "animation": "scroll_left"}).result(10)
    uploads = [e for e in Panel.log if e[0] == "upload"]
    assert {u[1] for u in uploads} == {"L", "R"}
    assert all((u[2].width, u[2].height) == (32, 16) for u in uploads)
    assert uploads[0][2].frames == uploads[1][2].frames > 1  # same frame count: they stay in step
    replays = [e for e in Panel.log if e[0] == "replay"]
    assert len(replays) == 2 and abs(replays[0][2] - replays[1][2]) < 0.5

    m.run(spec.address, "set_brightness", {"level": 40}).result(5)
    assert sorted(e[1] for e in Panel.log if e[0] == "brightness") == ["L", "R"]


def test_presets_and_unsupported_commands_on_group(wall):
    m, spec = wall
    m.connect(spec.address).result(5)
    m.run_preset(spec.address, presets.find("Trans flag")).result(10)
    tops = [e[2] for e in Panel.log if e[0] == "upload"]
    assert len(tops) == 2 and all(t.ext == ".png" for t in tops)
    with pytest.raises(RuntimeError, match="doesn't support"):
        m.run(spec.address, "set_clock_mode", {}).result(5)


def test_mismatched_sizes_are_refused():
    m = DeviceManager()
    sizes = {"A": PanelInfo(32, 16, 3), "B": PanelInfo(64, 16, 3)}
    m._client_factory = lambda a: m._make_client(a) if groups.is_group(a) else Panel(a, sizes[a])
    spec = groups.GroupSpec("Bad", "bad", 2, 1, ["A", "B"])
    m.set_group(spec)
    try:
        with pytest.raises(RuntimeError, match="same size"):
            m.connect(spec.address).result(5)
    finally:
        m.shutdown()


def test_spec_validation():
    with pytest.raises(ValueError):
        groups.GroupSpec("x", "x", 2, 1, ["A"]).validate()
    with pytest.raises(ValueError):
        groups.GroupSpec("x", "x", 2, 1, ["A", "A"]).validate()


def test_animations_are_resynced_every_loop(wall):
    m, spec = wall
    m.connect(spec.address).result(5)
    frames = [Image.new("RGB", (64, 16), c) for c in ("red", "blue")]
    prepared = media.prepare_frames(frames, [100, 100])  # 0.2 s loop
    group = m._clients[spec.address]
    m.submit(group.send_prepared(prepared)).result(5)
    time.sleep(0.75)
    replays = [e for e in Panel.log if e[0] == "replay"]
    assert len(replays) >= 2 * 3  # initial restart + at least two loops, on both panels
    m.submit(group.disconnect()).result(5)
    n = len([e for e in Panel.log if e[0] == "replay"])
    time.sleep(0.35)
    assert len([e for e in Panel.log if e[0] == "replay"]) == n  # stops with the content


class _IPixel:
    """Client without replay_last – like pypixelcolor's."""

    def __init__(self, address):
        self.address = address
        self.is_connected = True
        self.shown = []

    async def connect(self):
        pass

    def get_device_info(self):
        return type("I", (), {"width": 32, "height": 16})()

    async def show_slot(self, number):
        self.shown.append(number)

    async def send_image_hex(self, **kw):
        pass


@pytest.mark.parametrize("slot", [None, 9])
def test_ipixel_group_sync_via_slot(slot):
    m = DeviceManager()
    clients = {}
    m._client_factory = lambda a: m._make_client(a) if groups.is_group(a) else clients.setdefault(a, _IPixel(a))
    spec = groups.GroupSpec("W", "w", 2, 1, ["A", "B"], sync_slot=slot)
    m.set_group(spec)
    events = []
    m.add_listener(lambda ev, d: events.append(ev))
    try:
        m.connect(spec.address).result(5)
        frames = [Image.new("RGB", (64, 16), c) for c in ("red", "blue")]
        m.submit(m._clients[spec.address].send_prepared(media.prepare_frames(frames, [100, 100]))).result(5)
        time.sleep(0.3)
        if slot:
            assert clients["A"].shown and clients["A"].shown[0] == 9 and "group_unsynced" not in events
        else:
            assert not clients["A"].shown and "group_unsynced" in events
    finally:
        m.shutdown()
