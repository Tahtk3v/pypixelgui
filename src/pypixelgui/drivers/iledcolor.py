"""Bluetooth client for iLEDColor panels (badges, collars, backpacks, signs).

Offers the same shape as pypixelcolor's ``AsyncClient`` for the commands these
panels support, so the rest of the app can treat both kinds alike. The panel
firmware has no text or clock rendering of its own: text is drawn here and sent
as an image/animation.
"""

from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass
from typing import Callable

from PIL import Image, ImageSequence

from .. import media, render
from . import iledcolor_protocol as p

log = logging.getLogger(__name__)

KIND = "iledcolor"
#: Commands (pypixelgui names) this driver implements.
CAPABILITIES = frozenset({"send_text", "send_image", "send_image_b64", "set_brightness", "set_power"})

Progress = Callable[[int, int, int, int], None]  # (index, count, bytes_sent, bytes_total)


@dataclass
class ILedDeviceInfo:
    width: int
    height: int
    color_type: int
    firmware: int
    function_bits: int
    password_flag: int  # 255 = none / not supported, 1 = set
    kind: str = KIND
    led_type: str = "iLEDColor"


class ILedError(RuntimeError):
    pass


class ILedColorClient:
    kind = KIND
    capabilities = CAPABILITIES

    def __init__(self, address: str, panel: p.PanelInfo, password: str = "",
                 client_factory=None, reply_timeout: float = 3.0):
        self.address = address
        self.panel = panel
        self.password = password or ""
        self._client_factory = client_factory
        self._client = None
        self._replies: asyncio.Queue[p.Reply] = asyncio.Queue()
        self._timeout = reply_timeout
        self.is_connected = False
        self.password_set = False

    # --- connection ---------------------------------------------------------

    async def connect(self) -> None:
        if self._client_factory is None:
            from bleak import BleakClient
            self._client = BleakClient(self.address, disconnected_callback=self._on_disconnect)
        else:
            self._client = self._client_factory(self.address, self._on_disconnect)
        await self._client.connect()
        self.is_connected = True
        # BlueZ reports the default 23-byte MTU until asked; ask so uploads use big chunks.
        acquire = getattr(getattr(self._client, "_backend", None), "_acquire_mtu", None)
        if acquire is not None:
            try:
                await acquire()
            except Exception:
                log.debug("could not acquire MTU", exc_info=True)
        try:
            await self._client.start_notify(p.CHAR_NOTIFY, self._on_notify)
            await self._handshake()
        except Exception:
            await self.disconnect()
            raise

    async def disconnect(self) -> None:
        client, self._client = self._client, None
        self.is_connected = False
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                log.debug("disconnect raised", exc_info=True)

    def _on_disconnect(self, *_):
        self.is_connected = False

    def _on_notify(self, _char, data) -> None:
        reply = p.parse_reply(bytes(data))
        if reply is not None:
            self._replies.put_nowait(reply)

    async def _handshake(self) -> None:
        await self._request(p.CHAR_CONTROL, p.connect_frame(), p.CMD_CONNECT)
        pw = p.password_bytes(self.password) if self.password else b"\x00" * 6
        reply = await self._request(p.CHAR_CONTROL, p.verify_password_frame(pw), p.CMD_VERIFY_PASSWORD)
        # 1 verified, 2 wrong / password set, 3 no password; some firmware answers 0 for "no password".
        self.password_set = reply.status in (p.PW_OK, p.PW_WRONG)
        if reply.status == p.PW_WRONG:
            log.warning("%s has a password and %s; uploads usually still work, settings may not",
                        self.address, "the saved one is wrong" if self.password else "none is saved")

    def get_device_info(self) -> ILedDeviceInfo:
        return ILedDeviceInfo(self.panel.width, self.panel.height, self.panel.color_type, self.panel.firmware,
                              self.panel.function_bits, 1 if self.password_set else 255)

    # --- request/response -------------------------------------------------------

    async def _write(self, char: str, frame: bytes) -> None:
        if self._client is None or not self.is_connected:
            raise ILedError("not connected")
        await self._client.write_gatt_char(char, frame, response=False)

    async def _request(self, char: str, frame: bytes, expect_cmd: int, timeout: float | None = None) -> p.Reply:
        while not self._replies.empty():  # drop stale notifications
            self._replies.get_nowait()
        await self._write(char, frame)
        deadline = asyncio.get_running_loop().time() + (timeout or self._timeout)
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise ILedError(f"no reply from the display (command 0x{expect_cmd:02x})")
            try:
                reply = await asyncio.wait_for(self._replies.get(), remaining)
            except asyncio.TimeoutError:
                continue
            if reply.cmd == expect_cmd:
                return reply
            log.debug("ignoring reply 0x%02x while waiting for 0x%02x", reply.cmd, expect_cmd)

    def _chunk_size(self) -> int:
        mtu = getattr(self._client, "mtu_size", 517) or 23
        # Each write must fit in one ATT packet: MTU - 3 (ATT header) - 12 (marker, cmd,
        # DL, wrap id, chunk length, checksum).
        return max(1, min(492, int(mtu) - 15))

    # --- settings -------------------------------------------------------------------

    async def set_brightness(self, level: int) -> None:
        """0–100 % → firmware 10 (dimmest) … 1 (brightest)."""
        level = max(0, min(100, int(level)))
        await self._request(p.CHAR_CONTROL, p.brightness_frame(1 + round((100 - level) * 9 / 100)),
                            p.CMD_BRIGHTNESS)

    async def set_power(self, on: bool = True) -> None:
        await self._request(p.CHAR_CONTROL, p.switch_frame(bool(on)), p.CMD_SWITCH)

    async def password_operation(self, mode: int, old: str = "", new: str = "") -> int:
        old_b = p.password_bytes(old) if old else b""
        new_b = p.password_bytes(new) if new else b""
        reply = await self._request(p.CHAR_CONTROL, p.password_op_frame(mode, old_b, new_b), p.CMD_PASSWORD)
        if reply.status != 1:
            raise ILedError({2: "the display rejected the change (wrong current password?)",
                             3: "the display refused to remove the password (wrong password?)"}
                            .get(reply.status, f"password operation failed (status {reply.status})"))
        self.password_set = mode != p.PW_CLEAR
        self.password = new if mode != p.PW_CLEAR else ""
        return reply.status

    # --- content ---------------------------------------------------------------------

    async def send_image_hex(self, hex_string: str, file_extension: str, resize_method: str = "crop",
                             save_slot: int = 0, **_ignored) -> None:
        prepared = await asyncio.to_thread(media.prepare, bytes.fromhex(hex_string), self.panel.width,
                                           self.panel.height, resize_method)
        await self.send_prepared(prepared)

    async def send_image(self, path: str, resize_method: str = "crop", save_slot: int = 0, **_ignored) -> None:
        prepared = await asyncio.to_thread(media.prepare, path, self.panel.width, self.panel.height,
                                           resize_method)
        await self.send_prepared(prepared)

    async def send_text(self, text: str, color: str = "ffffff", bg_color: str | None = None,
                        animation: str = "scroll_left", speed: int = 80, **_ignored) -> None:
        w, h = self.panel.width, self.panel.height
        frames = render.text_frames(text, w, h, color or "ffffff", bg=bg_color or "000000",
                                    scroll=_scroll_mode(animation), scroll_ms=_scroll_ms(speed))
        if str(animation).lower() == "blink":
            frames = render.blink(frames)
        data = render.encode_gif(frames)
        await self.send_prepared(media.PreparedImage(data, ".gif", w, h, len(frames), len(frames), len(data)))

    async def send_prepared(self, prepared: media.PreparedImage, save_slot: int = 0,
                            progress: Progress | None = None) -> None:
        pkg, pid = await asyncio.to_thread(self._package, prepared)
        await self._upload(pkg, pid, progress)

    def _package(self, prepared: media.PreparedImage) -> tuple[bytes, int]:
        w, h, ct = self.panel.width, self.panel.height, self.panel.color_type
        frames, durations = _decode(prepared.data, w, h)
        if len(frames) == 1:
            content = p.pack_pixels(frames[0], w, h, ct)
            return p.program([p.partition(w, h, p.TYPE_BITMAP, 1, content)])
        if not self.panel.is_mono and (self.panel.supports_raw_gif or not self.panel.function_bits):
            return p.program([p.raw_gif_partition(w, h, prepared.data)])
        # Decoded frames (type 2): one dwell for all frames, in 20 ms ticks.
        tick = max(1, min(255, round(min(durations) / 20)))
        content = bytearray()
        for f, d in zip(frames, durations):
            packed = p.pack_pixels(f, w, h, ct) + (b"" if self.panel.is_mono else b"\x00\x01")
            content += packed * max(1, round(d / (tick * 20)))
        count = len(content) // (len(p.pack_pixels(frames[0], w, h, ct)) + (0 if self.panel.is_mono else 2))
        return p.program([p.partition(w, h, p.TYPE_FRAMES, count, bytes(content), residence=tick)])

    async def _upload(self, pkg: bytes, pid: int, progress: Progress | None) -> None:
        reply = await self._request(p.CHAR_CONTROL, p.announce_frame(pid, len(pkg)), p.CMD_ANNOUNCE)
        if reply.status == p.ANNOUNCE_CACHED:
            log.info("display already has this content; switching to it without re-uploading")
            if progress:
                progress(1, 1, len(pkg), len(pkg))
            return
        if reply.status == p.ANNOUNCE_FULL:
            raise ILedError("the display's storage is full")
        if reply.status not in (p.ANNOUNCE_PROCEED,):
            log.warning("unexpected announce reply %s; trying anyway", reply.status)
        size = self._chunk_size()
        chunks = [pkg[i:i + size] for i in range(0, len(pkg), size)]
        sent = 0
        for wrap, chunk in enumerate(chunks):
            if progress:
                progress(wrap, len(chunks), sent, len(pkg))
            ack = await self._request(p.CHAR_BULK, p.fragment_frame(wrap, chunk), p.CMD_FRAGMENT)
            if ack.wrap_id != wrap or ack.status != 1:
                raise ILedError(f"display rejected part {wrap + 1} of {len(chunks)} (status {ack.status})")
            sent += len(chunk)
        done = await self._request(p.CHAR_BULK, p.upload_done_frame(), p.CMD_UPLOAD_DONE, timeout=8)
        if progress:
            progress(len(chunks), len(chunks), len(pkg), len(pkg))
        if done.status != 1:
            raise ILedError(f"display did not accept the upload (status {done.status})")


def _scroll_mode(animation) -> bool | None:
    a = str(animation).lower()
    if a in ("scroll_left", "scroll_right", "1", "2"):
        return True
    if a in ("static", "0"):
        return False
    return None


def _scroll_ms(speed: int) -> int:
    """pypixelcolor speed 0–100 (higher = faster) → ms per scroll step."""
    return int(140 - max(0, min(100, int(speed))) * 1.0)


def _decode(data: bytes, width: int, height: int) -> tuple[list[bytes], list[int]]:
    im = Image.open(io.BytesIO(data))
    frames, durations = [], []
    for f in ImageSequence.Iterator(im):
        frames.append(f.convert("RGB").resize((width, height)).tobytes())
        durations.append(int(f.info.get("duration", 100) or 100))
    return frames, durations
