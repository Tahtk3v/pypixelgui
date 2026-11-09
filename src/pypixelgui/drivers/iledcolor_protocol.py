"""Wire format for iLEDColor panels (Bluetooth service 0xA950).

Pure functions only – no Bluetooth – so everything here is unit-tested against
published test vectors.

Protocol knowledge comes from these MIT-licensed projects (see
THIRD_PARTY_NOTICES.md); this module is an independent implementation:

* set4npl/ledsign – PROTOCOL.md, test vectors, gamma table
* zagi/iledcolor_small – handshake/upload flow and raw-GIF partition header
* gwgorman/iledcolor-badge-mac – monochrome "column16" pixel packing
* akkaisinabin/iledcolor-rs (no licence; used only as a protocol description)

Frame:   0x54 | cmd | DL:u16be | data | checksum:u16be
         DL = len(data) + 2; checksum = 16-bit sum of all preceding bytes.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

SERVICE = "0000a950-0000-1000-8000-00805f9b34fb"
CHAR_CONTROL = "0000a951-0000-1000-8000-00805f9b34fb"
CHAR_BULK = "0000a952-0000-1000-8000-00805f9b34fb"
CHAR_NOTIFY = "0000a953-0000-1000-8000-00805f9b34fb"

MARKER = 0x54
COMPANY_CODE = 0x5442

CMD_FRAGMENT = 0x00
CMD_UPLOAD_DONE = 0x01
CMD_DELETE_ALL = 0x02
CMD_ANNOUNCE = 0x06
CMD_BRIGHTNESS = 0x09
CMD_SWITCH = 0x0A
CMD_CONNECT = 0x0D
CMD_PASSWORD = 0x0E
CMD_VERIFY_PASSWORD = 0x0F

# Announce (cmd 06) replies
ANNOUNCE_PROCEED, ANNOUNCE_FULL, ANNOUNCE_CACHED = 1, 2, 3
# Verify password (cmd 0F) replies
PW_OK, PW_WRONG, PW_NOT_ENABLED = 1, 2, 3
# Password operations (cmd 0E)
PW_SET, PW_CHANGE, PW_CLEAR = 0, 1, 2

# Partition types
TYPE_BITMAP, TYPE_FRAMES, TYPE_RAW_GIF = 1, 2, 6
# Colour types from the advertisement
COLOR_MONO = (0, 1, 2)          # 2 = "rainbow" modules: fixed-colour LEDs, driven like mono
COLOR_RGB888, COLOR_RGB565, COLOR_RGB332 = 3, 4, 5

FUNC_PASSWORD = 0x40
FUNC_RAW_GIF = 0x04


def checksum(data: bytes) -> int:
    return sum(data) & 0xFFFF


def build_frame(cmd: int, data: bytes) -> bytes:
    body = bytes([MARKER, cmd]) + (len(data) + 2).to_bytes(2, "big") + data
    return body + checksum(body).to_bytes(2, "big")


@dataclass(frozen=True)
class Reply:
    cmd: int
    status: int
    data: bytes
    wrap_id: int | None = None   # fragment acks only


def parse_reply(raw: bytes) -> Reply | None:
    """Parse a notification. Fragment acks are the odd one out: DL = payload length only."""
    raw = bytes(raw)
    if len(raw) < 5 or raw[0] != MARKER:
        return None
    cmd = raw[1]
    if cmd == CMD_FRAGMENT and len(raw) >= 9:
        return Reply(cmd, raw[8], raw[4:9], wrap_id=int.from_bytes(raw[4:8], "big"))
    dl = int.from_bytes(raw[2:4], "big")
    data = raw[4:4 + max(dl - 2, 0)]
    return Reply(cmd, data[0] if data else 0, data)


# --- commands ---------------------------------------------------------------

def connect_frame() -> bytes:
    return build_frame(CMD_CONNECT, b"\x00")


def verify_password_frame(password: bytes = b"\x00" * 6) -> bytes:
    return build_frame(CMD_VERIFY_PASSWORD, _pw(password))


def password_op_frame(mode: int, old: bytes = b"", new: bytes = b"") -> bytes:
    return build_frame(CMD_PASSWORD, bytes([mode]) + _pw(old) + _pw(new))


def brightness_frame(level: int) -> bytes:
    """Firmware scale: 1 = brightest … 10 = dimmest."""
    return build_frame(CMD_BRIGHTNESS, bytes([max(0, min(10, level))]) + b"\x00" * 8)


def switch_frame(on: bool) -> bytes:
    return build_frame(CMD_SWITCH, bytes([1 if on else 0]) + b"\x00" * 8)


def announce_frame(program_id: int, length: int) -> bytes:
    return build_frame(CMD_ANNOUNCE, program_id.to_bytes(4, "big") + length.to_bytes(4, "big") + b"\x00" * 3)


def fragment_frame(wrap_id: int, chunk: bytes) -> bytes:
    return build_frame(CMD_FRAGMENT, wrap_id.to_bytes(4, "big") + len(chunk).to_bytes(2, "big") + chunk)


def upload_done_frame() -> bytes:
    return build_frame(CMD_UPLOAD_DONE, b"\x01")


def _pw(value: bytes) -> bytes:
    value = bytes(value or b"")
    if not value:
        return b"\x00" * 6
    if len(value) != 6:
        raise ValueError("iLEDColor passwords are exactly 6 characters")
    return value


def password_bytes(text: str) -> bytes:
    data = text.encode("ascii")
    if len(data) != 6:
        raise ValueError("iLEDColor passwords are exactly 6 characters (letters/digits)")
    return data


# --- program packages ------------------------------------------------------------

_CRC32C_TABLE = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = (_c >> 1) ^ (0x82F63B78 if _c & 1 else 0)
    _CRC32C_TABLE.append(_c)


def crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for b in data:
        crc = (crc >> 8) ^ _CRC32C_TABLE[(crc ^ b) & 0xFF]
    return crc ^ 0xFFFFFFFF


def partition(width: int, height: int, ptype: int, total_frames: int, content: bytes, *,
              effect: int = 0, velocity: int = 0, residence: int = 5, border: int = 0,
              luminance: int = 100, x: int = 0, y: int = 0) -> bytes:
    header = struct.pack(">HHHH", x, y, width, height) + b"\x00\x00\x00" + bytes([ptype])
    header += struct.pack(">H", total_frames)
    header += bytes([effect, velocity, residence, border, luminance]) + b"\x00\x00\x00"
    return header + content


def raw_gif_partition(width: int, height: int, gif: bytes) -> bytes:
    """Raw GIF (type 6); the panel decodes and loops it. Header bytes as used by
    zagi/iledcolor_small, which is confirmed working on hardware."""
    header = struct.pack(">HHHH", 0, 0, width, height)
    header += bytes.fromhex("0000" "0006" "0001" "0064" "0400" "0064" "0000")
    return header + gif


def program(partitions: list[bytes]) -> tuple[bytes, int]:
    """Package (24-byte header + partitions). Returns (package, program_id)."""
    offsets, pos = [], 0
    for p in partitions:
        offsets.append(pos)
        pos += len(p)
    offsets += [0] * (4 - len(offsets))
    content = b"".join(partitions)
    pid = crc32c(content)
    header = pid.to_bytes(4, "big") + bytes([len(partitions)]) + b"\x00\x00\x00"
    header += b"".join(o.to_bytes(4, "big") for o in offsets[:4])
    return header + content, pid


# --- pixels --------------------------------------------------------------------------

# Panel gamma (≈3.0), from set4npl/ledsign. Applied per channel right before packing.
GAMMA_LUT = bytes((
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1,
    1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 3, 3, 3,
    3, 3, 3, 4, 4, 4, 4, 4, 5, 5, 5, 5, 6, 6, 6, 6,
    7, 7, 7, 7, 8, 8, 8, 9, 9, 9, 10, 10, 10, 11, 11, 12,
    12, 12, 13, 13, 14, 14, 14, 15, 15, 16, 16, 17, 17, 18, 18, 19,
    20, 20, 21, 21, 22, 22, 23, 24, 24, 25, 26, 26, 27, 28, 28, 29,
    30, 30, 31, 32, 33, 34, 34, 35, 36, 37, 38, 38, 39, 40, 41, 42,
    43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 59,
    60, 61, 62, 63, 64, 66, 67, 68, 70, 71, 72, 73, 75, 76, 78, 79,
    80, 82, 83, 85, 86, 88, 89, 91, 92, 94, 95, 97, 99, 100, 102, 104,
    105, 107, 109, 110, 112, 114, 116, 118, 120, 121, 123, 125, 127, 129, 131, 133,
    135, 137, 139, 141, 143, 145, 147, 150, 152, 154, 156, 158, 161, 163, 165, 168,
    170, 172, 175, 177, 179, 182, 184, 187, 189, 192, 195, 197, 200, 202, 205, 208,
    210, 213, 216, 219, 222, 224, 227, 230, 233, 236, 239, 242, 245, 248, 251, 254,
))


def pack_pixels(rgb: bytes, width: int, height: int, color_type: int) -> bytes:
    """Row-major RGB888 → the panel's pixel format."""
    if color_type in COLOR_MONO:
        return pack_mono_column16(rgb, width, height)
    g = rgb.translate(GAMMA_LUT)
    if color_type == COLOR_RGB565:
        out = bytearray()
        for i in range(0, len(g), 3):
            r, gg, b = g[i], g[i + 1], g[i + 2]
            out += bytes(((r & 0xF8) | (gg >> 5), ((gg & 0x1C) << 3) | (b >> 3)))
        return bytes(out)
    if color_type == COLOR_RGB332:
        return bytes((g[i] & 0xE0) | ((g[i + 1] & 0xE0) >> 3) | ((g[i + 2] & 0xC0) >> 6)
                     for i in range(0, len(g), 3))
    return g  # RGB888


def pack_mono_column16(rgb: bytes, width: int, height: int, threshold: int = 96) -> bytes:
    """Monochrome: for each column, one MSB-first byte per 8-row band (top row = MSB)."""
    def lit(x: int, y: int) -> bool:
        i = (y * width + x) * 3
        return max(rgb[i], rgb[i + 1], rgb[i + 2]) >= threshold
    out = bytearray()
    for x in range(width):
        for band in range(0, height, 8):
            byte = 0
            for bit in range(8):
                if band + bit < height and lit(x, band + bit):
                    byte |= 1 << (7 - bit)
            out.append(byte)
    return bytes(out)


# --- advertisement ------------------------------------------------------------------

@dataclass(frozen=True)
class PanelInfo:
    width: int
    height: int
    color_type: int
    rotation: int = 0
    firmware: int = 0
    function_bits: int = 0
    app_code: int = 0

    @property
    def has_password_support(self) -> bool:
        return bool(self.function_bits & FUNC_PASSWORD)

    @property
    def supports_raw_gif(self) -> bool:
        return bool(self.function_bits & FUNC_RAW_GIF)

    @property
    def is_mono(self) -> bool:
        return self.color_type in COLOR_MONO

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, d: dict | None) -> "PanelInfo | None":
        if not d:
            return None
        return cls(**{k: int(v) for k, v in d.items() if k in cls.__dataclass_fields__})


def parse_advertisement(payload: bytes) -> PanelInfo | None:
    """Parse ``category | company(0x5442) | app | height | width | color | rotation | fw | client | func``."""
    if len(payload) < 17 or int.from_bytes(payload[1:3], "big") != COMPANY_CODE:
        return None
    return PanelInfo(
        app_code=int.from_bytes(payload[3:5], "big"),
        height=int.from_bytes(payload[5:7], "big"),
        width=int.from_bytes(payload[7:9], "big"),
        color_type=payload[9],
        rotation=payload[10],
        firmware=int.from_bytes(payload[11:13], "big"),
        function_bits=int.from_bytes(payload[15:17], "big"),
    )


def panel_from_manufacturer_data(manufacturer_data: dict[int, bytes]) -> PanelInfo | None:
    """bleak splits off a little-endian company id; put those two bytes back first."""
    for company_id, data in manufacturer_data.items():
        info = parse_advertisement(company_id.to_bytes(2, "little") + bytes(data))
        if info is not None:
            return info
    return None
