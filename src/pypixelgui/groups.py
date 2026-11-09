"""Display groups: several identical panels used as one bigger display.

A group is a virtual device with the address ``group:<alias>``. Content is
drawn at the combined size, cut into one tile per panel and uploaded to all
panels at once; brightness and power go to every panel. Because it behaves
like a device, presets, rotations and MQTT work on groups unchanged.

Animations: each panel starts playing when its own upload finishes, so after
all uploads are done the group restarts them together – and again at the start
of every loop of the animation, because two displays' clocks drift apart over
a long scroll. Restarting uses:

* iLEDColor – re-announcing the just-uploaded program makes the panel show it
  again from the start (it answers "already stored", nothing is re-sent);
* iPixel – only if the group has a ``sync_slot``: tiles are saved to that slot
  and ``show_slot`` is sent to all panels at the same moment.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from . import media, render

log = logging.getLogger(__name__)

PREFIX = "group:"
CAPABILITIES = frozenset({"send_text", "send_image", "send_image_b64", "set_brightness", "set_power"})


def address_of(alias: str) -> str:
    return PREFIX + alias


def is_group(address: str) -> bool:
    return bool(address) and address.startswith(PREFIX)


@dataclass
class GroupSpec:
    name: str
    alias: str
    cols: int
    rows: int
    #: Member addresses, row by row, left to right.
    members: list[str] = field(default_factory=list)
    #: iPixel only: slot used to start animations on all panels at once (None = off).
    sync_slot: int | None = None
    hold: bool = False

    @property
    def address(self) -> str:
        return address_of(self.alias)

    @classmethod
    def from_dict(cls, d: dict) -> "GroupSpec":
        return cls(name=d["name"], alias=d["alias"], cols=int(d["cols"]), rows=int(d["rows"]),
                   members=list(d.get("members", [])), sync_slot=d.get("sync_slot"),
                   hold=bool(d.get("hold", False)))

    def to_dict(self) -> dict:
        return {"name": self.name, "alias": self.alias, "cols": self.cols, "rows": self.rows,
                "members": list(self.members), "sync_slot": self.sync_slot, "hold": self.hold}

    def validate(self) -> None:
        if self.cols < 1 or self.rows < 1:
            raise ValueError("a group needs at least one column and one row")
        if len(self.members) != self.cols * self.rows:
            raise ValueError(f"{self.cols}×{self.rows} needs {self.cols * self.rows} displays, "
                             f"got {len(self.members)}")
        if len(set(self.members)) != len(self.members):
            raise ValueError("a display can only appear once in a group")


@dataclass
class GroupInfo:
    width: int
    height: int
    tile_width: int
    tile_height: int
    panels: int
    kind: str = "group"
    led_type: str = "group"
    password_flag: int = 255


class GroupClient:
    """Drives the member displays through the DeviceManager (sharing their connections and locks)."""

    capabilities = CAPABILITIES

    def __init__(self, manager, spec: GroupSpec):
        spec.validate()
        self.manager = manager
        self.spec = spec
        self._info: GroupInfo | None = None
        self._resync: asyncio.Task | None = None

    # --- connection ---------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return all(self.manager.is_connected(a) for a in self.spec.members)

    async def connect(self) -> None:
        await asyncio.gather(*(self.manager._connect(a) for a in self.spec.members))
        sizes = {(i.width, i.height) for i in (self.manager.device_info(a) for a in self.spec.members) if i}
        if len(sizes) != 1:
            raise RuntimeError(f"all displays in a group must be the same size, got {sorted(sizes)}")
        (tw, th), = sizes
        self._info = GroupInfo(tw * self.spec.cols, th * self.spec.rows, tw, th, len(self.spec.members))

    async def disconnect(self) -> None:
        # Members stay connected: they may be held, or used on their own.
        self._stop_resync()
        self._info = None

    def _stop_resync(self) -> None:
        if self._resync is not None:
            self._resync.cancel()
            self._resync = None

    def can_sync(self) -> bool:
        """True when every panel can be restarted on command."""
        for address in self.spec.members:
            client = self.manager._clients.get(address)
            if not hasattr(type(client), "replay_last") and not self.spec.sync_slot:
                return False
        return True

    def get_device_info(self) -> GroupInfo | None:
        return self._info

    # --- broadcast commands -----------------------------------------------------------

    async def set_brightness(self, level: int) -> None:
        await self._each("set_brightness", {"level": level})

    async def set_power(self, on: bool = True) -> None:
        await self._each("set_power", {"on": on})

    async def _each(self, command: str, params: dict) -> None:
        await asyncio.gather(*(self.manager._run(a, command, params, False, True) for a in self.spec.members))

    # --- content ---------------------------------------------------------------------------

    async def send_text(self, text: str, color: str = "ffffff", bg_color: str | None = None,
                        animation: str = "scroll_left", speed: int = 80, **_ignored) -> None:
        from .drivers.iledcolor import _scroll_mode, _scroll_ms  # same text rules as iLEDColor
        info = self._require_info()
        frames = render.text_frames(text, info.width, info.height, color or "ffffff", bg=bg_color or "000000",
                                    scroll=_scroll_mode(animation), scroll_ms=_scroll_ms(speed))
        if str(animation).lower() == "blink":
            frames = render.blink(frames)
        imgs = [f for f, _ in frames]
        prepared = await asyncio.to_thread(media.prepare_frames, imgs, [d for _, d in frames],
                                           media.DEFAULT_BUDGET_KB * info.panels)
        await self.send_prepared(prepared)

    async def send_image_hex(self, hex_string: str, file_extension: str, resize_method: str = "crop",
                             save_slot: int = 0, **_ignored) -> None:
        await self._send_source(bytes.fromhex(hex_string), resize_method)

    async def send_image(self, path: str, resize_method: str = "crop", save_slot: int = 0, **_ignored) -> None:
        await self._send_source(path, resize_method)

    async def _send_source(self, source, resize_method: str) -> None:
        info = self._require_info()
        prepared = await asyncio.to_thread(media.prepare, source, info.width, info.height, resize_method,
                                           media.DEFAULT_BUDGET_KB * info.panels)
        await self.send_prepared(prepared)

    async def send_prepared(self, prepared: media.PreparedImage, save_slot: int = 0, progress=None) -> None:
        info = self._require_info()
        if (prepared.width, prepared.height) != (info.width, info.height):
            prepared = await asyncio.to_thread(media.prepare, prepared.data, info.width, info.height, "crop",
                                               media.DEFAULT_BUDGET_KB * info.panels)
        self._stop_resync()  # new content: the old animation's re-syncing ends
        tiles = await asyncio.to_thread(media.split, prepared, self.spec.cols, self.spec.rows)
        animated = prepared.ext == ".gif"
        slot = self.spec.sync_slot if animated else None

        async def upload(address: str, tile: media.PreparedImage) -> None:
            client = self.manager._require(address, "send_image")
            lock = self.manager._cmd_locks.setdefault(address, asyncio.Lock())
            async with lock:
                await self.manager._clean_if_dirty(address)
                client = self.manager._require(address, "send_image")
                use_slot = slot if slot and self.manager.kind(address) == "ipixel" else 0
                await self.manager._uploading(address, self.manager._send_prepared(client, address, tile, use_slot))

        await asyncio.gather(*(upload(a, t) for a, t in zip(self.spec.members, tiles)))
        if progress:
            progress(1, 1, len(prepared.data), len(prepared.data))
        if animated:
            if not self.can_sync():
                self.manager._emit("group_unsynced", address=self.spec.address)
                return
            await self._restart_together(slot)
            cycle = sum(media.decode(prepared)[1]) / 1000
            if cycle > 0:
                self._resync = asyncio.get_running_loop().create_task(self._keep_in_step(slot, cycle))

    async def _keep_in_step(self, slot: int | None, cycle: float) -> None:
        """Restart all panels together at the start of every loop of the animation."""
        while True:
            await asyncio.sleep(cycle)
            await self._restart_together(slot)

    async def _restart_together(self, slot: int | None) -> None:
        async def restart(address: str) -> None:
            client = self.manager._clients.get(address)
            lock = self.manager._cmd_locks.setdefault(address, asyncio.Lock())
            async with lock:
                if hasattr(type(client), "replay_last"):
                    await client.replay_last()
                elif slot and hasattr(client, "show_slot"):
                    await client.show_slot(slot)
        try:
            await asyncio.gather(*(restart(a) for a in self.spec.members))
        except Exception:
            log.warning("could not restart the group's animation together", exc_info=True)

    def _require_info(self) -> GroupInfo:
        if self._info is None:
            raise RuntimeError("group not connected")
        return self._info
