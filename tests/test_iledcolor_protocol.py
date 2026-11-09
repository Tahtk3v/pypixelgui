"""iLEDColor wire format, checked against vectors published by set4npl/ledsign (MIT)."""

import pytest

from pypixelgui.drivers import iledcolor_protocol as p

TYPE2_PACKAGE = bytes.fromhex(
    "0675c761010000000000000000000000000000000000000000000000000800080000000200020000320064000000"
    "030507060a0e090f150c141c0f1923121e2a1523311828381b2d3f1e324621374d243c5427415b2a46622d4b6930"
    "5070335577365a7e395f853c648c3f6993426e9a4573a14878a84b7daf4e82b65187bd548cc45791cb5a96d25d9b"
    "d960a0e063a5e766aaee69aff56cb4fc6fb90372be0a75c31178c8187bcd1f7ed22681d72d84dc3487e13b8ae642"
    "8deb4990f05093f55796fa5e99ff659c046c9f0973a20e7aa51381a81888ab1d8fae2296b1279db42ca4b731abba"
    "36b2bd3bb9c040c00001060a0e090f150c141c0f1923121e2a1523311828381b2d3f1e324621374d243c5427415b"
    "2a46622d4b69305070335577365a7e395f853c648c3f6993426e9a4573a14878a84b7daf4e82b65187bd548cc457"
    "91cb5a96d25d9bd960a0e063a5e766aaee69aff56cb4fc6fb90372be0a75c31178c8187bcd1f7ed22681d72d84dc"
    "3487e13b8ae6428deb4990f05093f55796fa5e99ff659c046c9f0973a20e7aa51381a81888ab1d8fae2296b1279d"
    "b42ca4b731abba36b2bd3bb9c040c0c345c70001")


@pytest.mark.parametrize("frame,expected", [
    (p.upload_done_frame(), "54010003010059"),
    (p.verify_password_frame(), "540f0008000000000000006b"),
    (p.verify_password_frame(b"123456"), "540f000831323334353601a0"),
    (p.announce_frame(0xCA9085E7, 0x122E), "5406000dca9085e70000122e000000036d"),
    (p.connect_frame(), "540d0003000064"),
    (p.fragment_frame(0, bytes.fromhex("deadbeef")), "5400000c000000000004deadbeef039c"),
    (p.password_op_frame(p.PW_SET, b"", b"123456"), "540e000f0000000000000031323334353601a6"),
    (p.password_op_frame(p.PW_CHANGE, b"123456", b"654321"), "540e000f0131323334353636353433323102dc"),
    (p.password_op_frame(p.PW_CLEAR, b"123456"), "540e000f0231323334353600000000000001a8"),
])
def test_frames_match_vectors(frame, expected):
    assert frame.hex() == expected


@pytest.mark.parametrize("raw,cmd,status,wrap", [
    ("540000050000000001005a", 0, 1, 0),   # fragment ack: DL is payload-only
    ("54010003010059", 1, 1, None),
    ("5403000303005d", 3, 3, None),
    ("540f0003020068", 15, 2, None),
])
def test_replies_parse(raw, cmd, status, wrap):
    r = p.parse_reply(bytes.fromhex(raw))
    assert (r.cmd, r.status, r.wrap_id) == (cmd, status, wrap)


def test_crc32c_and_bitmap_program():
    assert p.crc32c(b"123456789") == 0xE3069283  # standard CRC-32C check value
    content = bytes([0xFF, 0, 0]) * (96 * 16)
    pkg, pid = p.program([p.partition(96, 16, p.TYPE_BITMAP, 1, content)])
    assert (pid, len(pkg)) == (0x93AB2B5E, 4654)


def test_frames_program_rebuilds_vector():
    content = TYPE2_PACKAGE[24 + 22:]
    pkg, pid = p.program([p.partition(8, 8, p.TYPE_FRAMES, 2, content, residence=50)])
    assert pid == 0x0675C761 and pkg == TYPE2_PACKAGE


def test_gamma_and_pixel_formats():
    grey = bytes([128, 128, 128]) * 4
    assert p.pack_pixels(grey, 2, 2, p.COLOR_RGB888) == bytes([0x1E]) * 12
    white = bytes([255, 255, 255])
    assert p.pack_pixels(white, 1, 1, p.COLOR_RGB565) == bytes([0xFF, 0xFF])  # gamma 255 → 254, still all bits
    assert p.pack_pixels(white, 1, 1, p.COLOR_RGB332) == bytes([0xFF])


def test_mono_column16_packing():
    # 48x12, all lit: each column = 0xFF (rows 0-7) + 0xF0 (rows 8-11) – the badge's verified pattern
    rgb = bytes([255, 255, 255]) * (48 * 12)
    assert p.pack_pixels(rgb, 48, 12, 1) == bytes([0xFF, 0xF0]) * 48


@pytest.mark.parametrize("payload,expect", [
    ("0154424402001000400300000900020094000000", (64, 16, 3, 0x94)),
    ("0154424401001000600500000e00d300f7000000", (96, 16, 5, 0xF7)),
    ("0154424402000c00300100000100000004", (48, 12, 1, 0x04)),
    ("015442447700100040030000090002009400", (64, 16, 3, 0x94)),
    ("01ff42440200100040030000090002009400", None),
    ("0154424402001000", None),
])
def test_advertisement(payload, expect):
    info = p.parse_advertisement(bytes.fromhex(payload))
    got = None if info is None else (info.width, info.height, info.color_type, info.function_bits)
    assert got == expect


def test_bleak_split_company_id_is_reassembled():
    info = p.panel_from_manufacturer_data({21505: bytes.fromhex("424402001000400300000900020094000000")})
    assert (info.width, info.height) == (64, 16)
