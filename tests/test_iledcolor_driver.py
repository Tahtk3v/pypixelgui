"""ILedColorClient against a simulated panel."""

import asyncio
import io

import pytest
from PIL import Image

from pypixelgui import media
from pypixelgui.drivers import iledcolor_protocol as p
from pypixelgui.drivers.iledcolor import ILedColorClient, ILedError


class FakePanel:
    """Answers like a panel: status replies on control/bulk, per-fragment acks."""

    def __init__(self, address, on_disconnect, *, password=b"", announce_status=1, mtu=517):
        self.writes = []
        self.notify = None
        self.password = password
        self.announce_status = announce_status
        self.mtu_size = mtu
        self.received = bytearray()

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def start_notify(self, char, cb):
        assert char == p.CHAR_NOTIFY
        self.notify = cb

    def _reply(self, cmd, data):
        self.notify(None, bytearray(p.build_frame(cmd, data)))

    async def write_gatt_char(self, char, frame, response=False):
        assert response is False
        self.writes.append((char, frame))
        cmd, data = frame[1], frame[4:-2]
        await asyncio.sleep(0)
        if cmd == p.CMD_CONNECT:
            self._reply(cmd, b"\x00\xba")
        elif cmd == p.CMD_VERIFY_PASSWORD:
            if not self.password:
                self._reply(cmd, b"\x03")
            else:
                self._reply(cmd, b"\x01" if data == self.password else b"\x02")
        elif cmd == p.CMD_ANNOUNCE:
            self._reply(cmd, bytes([self.announce_status]))
        elif cmd == p.CMD_FRAGMENT:
            assert char == p.CHAR_BULK
            self.received += data[6:]
            ack = bytes([0x54, 0, 0, 5]) + data[:4] + b"\x01"
            self.notify(None, bytearray(ack + p.checksum(ack).to_bytes(2, "big")))
        elif cmd == p.CMD_PASSWORD:
            self._reply(cmd, b"\x01")
        else:
            self._reply(cmd, b"\x01")


def _client(panel_kwargs=None, **kw):
    info = p.PanelInfo(width=64, height=16, color_type=3, function_bits=0x94)
    holder = {}

    def factory(address, on_disc):
        holder["panel"] = FakePanel(address, on_disc, **(panel_kwargs or {}))
        return holder["panel"]
    return ILedColorClient("AA", info, client_factory=factory, **kw), holder


def run(coro):
    return asyncio.run(coro)


def test_handshake_and_bitmap_upload_round_trip():
    client, h = _client()

    async def go():
        await client.connect()
        im = Image.new("RGB", (64, 16), "red")
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        await client.send_prepared(media.prepare(buf.getvalue(), 64, 16))
    run(go())
    panel = h["panel"]
    cmds = [f[1] for _, f in panel.writes]
    assert cmds[:3] == [p.CMD_CONNECT, p.CMD_VERIFY_PASSWORD, p.CMD_ANNOUNCE]
    assert cmds[-1] == p.CMD_UPLOAD_DONE
    # the panel received exactly the announced package, and its id is the CRC of the content
    announce = next(f for _, f in panel.writes if f[1] == p.CMD_ANNOUNCE)[4:-2]
    pkg = bytes(panel.received)
    assert int.from_bytes(announce[4:8], "big") == len(pkg)
    assert int.from_bytes(announce[:4], "big") == p.crc32c(pkg[24:])
    assert pkg[24 + 11] == p.TYPE_BITMAP and pkg[24 + 22:24 + 25] == bytes([254, 0, 0])  # gamma'd red


def test_small_mtu_means_small_fragments():
    client, h = _client({"mtu": 23})

    async def go():
        await client.connect()
        await client.send_text("Hi", color="00ff00", animation="static")
    run(go())
    frags = [f for _, f in h["panel"].writes if f[1] == p.CMD_FRAGMENT]
    assert frags and max(len(f) for f in frags) <= 23 - 3


def test_animation_uses_raw_gif_when_supported():
    client, h = _client()
    run(client.connect())
    run_text = client.send_text("A long scrolling message", color="ffffff", animation="scroll_left")
    run(run_text)
    pkg = bytes(h["panel"].received)
    assert pkg[24 + 11] == p.TYPE_RAW_GIF and pkg[24 + 22:24 + 25] == b"GIF"


def test_cached_content_skips_upload_and_full_storage_errors():
    client, h = _client({"announce_status": 3})
    run(client.connect())
    run(client.send_text("x", animation="static"))
    assert not [f for _, f in h["panel"].writes if f[1] == p.CMD_FRAGMENT]

    client, h = _client({"announce_status": 2})
    run(client.connect())
    with pytest.raises(ILedError, match="full"):
        run(client.send_text("x", animation="static"))


def test_password_detection_and_brightness_mapping():
    client, h = _client({"password": b"123456"})
    run(client.connect())
    assert client.get_device_info().password_flag == 1
    run(client.set_brightness(100))
    run(client.set_brightness(0))
    levels = [f[4] for _, f in h["panel"].writes if f[1] == p.CMD_BRIGHTNESS]
    assert levels == [1, 10]

    client, h = _client({"password": b"123456"}, password="123456")
    run(client.connect())
    run(client.password_operation(p.PW_CLEAR, old="123456"))
    assert client.get_device_info().password_flag == 255
