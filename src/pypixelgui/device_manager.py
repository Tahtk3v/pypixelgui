"""Toolkit-independent device control.

Runs one asyncio loop in a background thread, owns the BLE connections and
serialises commands per device. The GUI and the MQTT bridge both talk to a
:class:`DeviceManager`; neither touches Bluetooth directly.

"Hold" mode keeps a connection open and reconnects it if it drops. These
displays generally accept a single BLE connection at a time, so while this
app holds one, other phones/apps nearby cannot take control of the display.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from . import groups, media
from .commands import to_client_call, validate

#: Commands that can store their content in a device slot.
SLOT_COMMANDS = frozenset({"send_text", "send_image", "send_image_b64"})
#: Commands that change what the display shows.
CONTENT_COMMANDS = SLOT_COMMANDS | {"show_slot", "clear", "delete", "set_clock_mode"}


def _digest(steps) -> str:
    import hashlib
    import json
    return hashlib.sha1(json.dumps(steps, sort_keys=True, default=str).encode()).hexdigest()

log = logging.getLogger(__name__)

Listener = Callable[[str, dict], None]


class _QuietResize(logging.Filter):
    """We hand pypixelcolor finished images on purpose; hide its 'skipping resize' warning."""
    def filter(self, record: logging.LogRecord) -> bool:
        return "skipping image resizing" not in record.getMessage()


logging.getLogger("pypixelcolor.commands.send_image").addFilter(_QuietResize())


class _ProgressWindows(list):
    """List of transport windows that reports progress as pypixelcolor walks through it."""

    def __init__(self, windows, report: Callable[[int, int, int, int], None]):
        super().__init__(windows)
        self._report = report

    def __iter__(self):
        total_bytes = sum(len(getattr(w, "data", b"")) for w in list.__iter__(self))
        done = 0
        n = len(self)
        for i, w in enumerate(list.__iter__(self)):
            self._report(i, n, done, total_bytes)
            yield w
            done += len(getattr(w, "data", b""))
        self._report(n, n, total_bytes, total_bytes)


def _friendly_upload_error(error: Exception, still_connected: bool) -> str:
    text = str(error) or type(error).__name__
    if not still_connected:
        return (f"the display disconnected during the upload ({text}). Large animations are the usual "
                "cause – lower “Max size” on the Image tab and try again.")
    if "no ack" in text or "no_answer" in text:
        return (f"the display stopped answering ({text}). It may be out of memory for this animation – "
                "lower “Max size” on the Image tab and try again.")
    return text


#: Display families.
KIND_IPIXEL = "ipixel"        # iPixel Color, via pypixelcolor
KIND_ILEDCOLOR = "iledcolor"  # iLEDColor, via drivers.iledcolor


@dataclass
class FoundDevice:
    name: str
    address: str
    rssi: Optional[int] = None
    kind: str = KIND_IPIXEL
    #: iLEDColor panel geometry/features from the advertisement (dict form).
    panel: Optional[dict] = None


def classify(name: str, manufacturer_data: dict) -> tuple[str, Optional[dict]] | None:
    """Work out which kind of display an advertisement belongs to (None = not ours)."""
    from .drivers.iledcolor_protocol import panel_from_manufacturer_data
    panel = panel_from_manufacturer_data(manufacturer_data or {})
    if panel is not None:
        return KIND_ILEDCOLOR, panel.to_dict()
    if "LED" in (name or ""):  # the filter pypixelcolor uses
        return KIND_IPIXEL, None
    return None


async def _default_scan(timeout: float) -> list[FoundDevice]:
    from bleak import BleakScanner
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    out = []
    for dev, adv in found.values():
        name = dev.name or adv.local_name or ""
        kind = classify(name, adv.manufacturer_data)
        if kind:
            out.append(FoundDevice(name=name or "LED display", address=dev.address, rssi=adv.rssi,
                                   kind=kind[0], panel=kind[1]))
    return sorted(out, key=lambda d: -(d.rssi or -999))


class DeviceManager:
    """Owns BLE clients; every public method is thread-safe and returns a Future."""

    def __init__(self, client_factory: Optional[Callable[[str], Any]] = None,
                 scan_func: Callable[[float], Awaitable[list[FoundDevice]]] = _default_scan,
                 watchdog_interval: float = 5.0):
        self._client_factory = client_factory or self._make_client
        #: What we know about each address before connecting: kind, panel, password.
        self._hints: dict[str, dict] = {}
        self._groups: dict[str, groups.GroupSpec] = {}
        self._scan_func = scan_func
        self._watchdog_interval = watchdog_interval
        self._clients: dict[str, Any] = {}
        self._held: set[str] = set()
        self._rotations: dict[str, concurrent.futures.Future] = {}
        self._rotation_names: dict[str, str] = {}
        #: Fingerprint of the preset content each display shows (to skip re-uploads).
        self._shown: dict[str, str] = {}
        self._cmd_locks: dict[str, asyncio.Lock] = {}
        self._listeners: list[Listener] = []
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="pypixelgui-ble", daemon=True)
        self._thread.start()
        self._watchdog_task = self.submit(self._watchdog())

    # --- plumbing -------------------------------------------------------

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def submit(self, coro: Awaitable) -> concurrent.futures.Future:
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def add_listener(self, listener: Listener) -> None:
        """Register ``listener(event, data)``. Called from the BLE thread."""
        self._listeners.append(listener)

    def remove_listener(self, listener: Listener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _emit(self, event: str, **data: Any) -> None:
        for listener in list(self._listeners):
            try:
                listener(event, data)
            except Exception:  # a broken listener must not kill the BLE loop
                log.exception("listener failed for %s", event)

    @staticmethod
    def _is_alive(client: Any) -> bool:
        session = getattr(client, "_session", None)
        if session is not None and hasattr(session, "is_connected"):
            return bool(session.is_connected)
        alive = getattr(client, "is_connected", None)
        if isinstance(alive, bool):
            return alive
        return bool(getattr(client, "_connected", True))

    # --- device kinds -------------------------------------------------------

    def remember(self, address: str, kind: str = KIND_IPIXEL, panel: Optional[dict] = None,
                 password: Optional[str] = None) -> None:
        """Record what kind of display lives at ``address`` (from a scan or saved settings)."""
        hint = self._hints.setdefault(address, {})
        hint["kind"] = kind or KIND_IPIXEL
        if panel:
            hint["panel"] = panel
        if password is not None:
            hint["password"] = password

    def kind(self, address: str) -> str:
        if groups.is_group(address):
            return "group"
        return self._hints.get(address, {}).get("kind", KIND_IPIXEL)

    # --- groups -------------------------------------------------------------

    def set_group(self, spec: "groups.GroupSpec") -> None:
        """Add or replace a display group (reachable at ``spec.address``)."""
        spec.validate()
        self._groups[spec.alias] = spec

    def remove_group(self, alias: str) -> concurrent.futures.Future:
        async def go():
            await self._disconnect(groups.address_of(alias))
            self._groups.pop(alias, None)
        return self.submit(go())

    def group(self, address: str) -> "groups.GroupSpec | None":
        return self._groups.get(address[len(groups.PREFIX):]) if groups.is_group(address) else None

    def capabilities(self, address: str) -> Optional[frozenset]:
        """Commands the connected display supports; None means "everything"."""
        client = self._clients.get(address)
        return getattr(type(client), "capabilities", None) if client is not None else None

    def _make_client(self, address: str):
        if groups.is_group(address):
            spec = self.group(address)
            if spec is None:
                raise RuntimeError(f"unknown group {address}")
            return groups.GroupClient(self, spec)
        hint = self._hints.get(address, {})
        if hint.get("kind") == KIND_ILEDCOLOR:
            from .drivers.iledcolor import ILedColorClient
            from .drivers.iledcolor_protocol import PanelInfo
            panel = PanelInfo.from_dict(hint.get("panel"))
            if panel is None:
                raise RuntimeError("panel size unknown – scan once so the app can read it")
            return ILedColorClient(address, panel, password=hint.get("password", ""))
        from pypixelcolor import AsyncClient
        return AsyncClient(address)

    def device_password(self, address: str, mode: int, old: str = "", new: str = "") -> concurrent.futures.Future:
        """Set (0) / change (1) / clear (2) an iLEDColor display's password."""
        async def go():
            client = self._require(address, "password")
            if not hasattr(type(client), "password_operation"):
                raise RuntimeError("this display has no password function")
            lock = self._cmd_locks.setdefault(address, asyncio.Lock())
            async with lock:
                await client.password_operation(mode, old, new)
            self._hints.setdefault(address, {})["password"] = new if mode != 2 else ""
            self._emit("password_changed", address=address, mode=mode)
        return self.submit(go())

    # --- public API (thread-safe) ----------------------------------------

    def scan(self, timeout: float = 5.0) -> concurrent.futures.Future:
        return self.submit(self._scan(timeout))

    def connect(self, address: str) -> concurrent.futures.Future:
        return self.submit(self._connect(address))

    def disconnect(self, address: str) -> concurrent.futures.Future:
        return self.submit(self._disconnect(address))

    def run(self, address: str, command: str, params: dict | None = None, *,
            remote: bool = False, allow_destructive: bool = True,
            connect: bool = False) -> concurrent.futures.Future:
        """Validate and run a command. Validation errors surface on the Future.

        ``connect=True`` connects first if needed (remote control, e.g. Home Assistant)."""
        async def go():
            if connect:
                await self._ensure_connected(address)
            return await self._run(address, command, params, remote, allow_destructive)
        return self.submit(go())

    def run_steps(self, address: str, steps: list[tuple[str, dict]], *, remote: bool = False,
                  allow_destructive: bool = True) -> concurrent.futures.Future:
        """Run several commands in order, stopping at the first failure (used by presets)."""
        async def _all():
            for command, params in steps:
                await self._run(address, command, params, remote, allow_destructive)
        return self.submit(_all())

    def run_preset(self, address: str, preset, *, remote: bool = False,
                   allow_destructive: bool = True, connect: bool = False) -> concurrent.futures.Future:
        """Render a :class:`~pypixelgui.presets.Preset` for this display's size and run it."""
        async def go():
            if connect:
                await self._ensure_connected(address)
            return await self._run_preset(address, preset, remote, allow_destructive)
        return self.submit(go())

    def show_preset(self, address: str, preset, *, remote: bool = False, allow_destructive: bool = True,
                    connect: bool = False) -> concurrent.futures.Future:
        """Show a preset. Live presets (``preset.refresh > 0``) then keep themselves
        up to date: re-checked every ``refresh`` seconds and re-uploaded only when
        what they'd show has changed. Anything sent afterwards stops that, just
        like a rotation (it *is* a one-item rotation)."""
        if getattr(preset, "refresh", 0) > 0:
            return self.start_rotation(address, [(preset, preset.refresh)], loop=True, remote=remote,
                                       allow_destructive=allow_destructive, connect=connect,
                                       skip_unchanged=True)
        return self.run_preset(address, preset, remote=remote, allow_destructive=allow_destructive,
                               connect=connect)

    async def _ensure_connected(self, address: str) -> None:
        if not self.is_connected(address):
            await self._connect(address)

    # --- rotation ---------------------------------------------------------

    def start_rotation(self, address: str, items: list[tuple[Any, float]], *, loop: bool = True,
                       preload_slot: int | None = None, remote: bool = False,
                       allow_destructive: bool = True, connect: bool = False,
                       name: str = "", skip_unchanged: bool = False) -> concurrent.futures.Future:
        """Cycle through ``(preset, seconds)`` items on one display.

        Replaces any rotation already running there. With ``preload_slot`` set,
        each item is first saved to the display's slots ``preload_slot``,
        ``preload_slot + 1``, … and the rotation then only sends ``show_slot``,
        which switches instantly instead of re-uploading every time.
        """
        if not items:
            raise ValueError("rotation is empty")
        self.stop_rotation(address)

        async def go():
            if connect:
                await self._ensure_connected(address)
            await self._rotation(address, list(items), loop, preload_slot, remote, allow_destructive,
                                 skip_unchanged)
        fut = self.submit(go())
        self._rotations[address] = fut
        self._rotation_names[address] = name
        self._emit("rotation_started", address=address, name=name)
        fut.add_done_callback(lambda f, a=address: self._rotation_done(a, f))
        return fut

    def stop_rotation(self, address: str) -> bool:
        fut = self._rotations.pop(address, None)
        if fut is None:
            return False
        fut.cancel()
        return True

    def rotation_name(self, address: str) -> str | None:
        return self._rotation_names.get(address) if self.rotation_running(address) else None

    def rotation_running(self, address: str) -> bool:
        fut = self._rotations.get(address)
        return fut is not None and not fut.done()

    def _rotation_done(self, address: str, fut: concurrent.futures.Future) -> None:
        if self._rotations.get(address) is fut:
            del self._rotations[address]
        self._emit("rotation_stopped", address=address,
                   message=None if fut.cancelled() or fut.exception() is None else str(fut.exception()))

    async def _rotation(self, address: str, items: list, loop: bool, preload_slot: int | None,
                        remote: bool, allow_destructive: bool, skip_unchanged: bool = False) -> None:
        total = len(items)
        slots: list[int] = []
        caps = self.capabilities(address)
        if preload_slot is not None and caps is not None and "show_slot" not in caps:
            preload_slot = None  # this display has no slots; re-send each item instead
        if preload_slot is not None:
            for i, (preset, _secs) in enumerate(items):
                slot = preload_slot + i
                self._emit("rotation_step", address=address, index=i, total=total,
                           name=f"Loading {preset.name} into slot {slot}")
                await self._run_preset(address, preset, remote, allow_destructive, save_slot=slot)
                slots.append(slot)
        while True:
            for i, (preset, secs) in enumerate(items):
                self._emit("rotation_step", address=address, index=i, total=total, name=preset.name)
                try:
                    if slots:
                        await self._run(address, "show_slot", {"number": slots[i]}, remote, allow_destructive)
                    else:
                        await self._run_preset(address, preset, remote, allow_destructive,
                                               skip_unchanged=skip_unchanged)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Usually a dropped link; with hold on it comes back, so keep going.
                    await asyncio.sleep(min(secs, 3))
                    continue
                await asyncio.sleep(max(0.5, float(secs)))
            if not loop:
                return

    def send_image_file(self, address: str, path: str, resize_method: str = "crop", save_slot: int = 0,
                        budget_kb: float = media.DEFAULT_BUDGET_KB) -> concurrent.futures.Future:
        """Shrink an image/GIF for this display (in a worker thread), then upload it with progress events."""
        return self.submit(self._send_image_file(address, path, resize_method, save_slot, budget_kb))

    def set_hold(self, address: str, hold: bool) -> concurrent.futures.Future:
        return self.submit(self._set_hold(address, hold))

    def is_connected(self, address: str) -> bool:
        c = self._clients.get(address)
        return c is not None and self._is_alive(c)

    def is_held(self, address: str) -> bool:
        return address in self._held

    def device_info(self, address: str):
        c = self._clients.get(address)
        return c.get_device_info() if c is not None else None

    def shutdown(self, timeout: float = 10.0) -> None:
        for addr in list(self._rotations):
            self.stop_rotation(addr)

        async def _close():
            self._held.clear()
            self._watchdog_task.cancel()
            for addr in list(self._clients):
                await self._disconnect(addr)
        try:
            self.submit(_close()).result(timeout)
        except Exception:
            log.exception("error during shutdown")
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2)

    # --- coroutines (BLE thread) -----------------------------------------

    async def _scan(self, timeout: float) -> list[FoundDevice]:
        self._emit("scan_started")
        try:
            devices = await self._scan_func(timeout)
        except Exception as e:
            self._emit("error", message=f"Scan failed: {e}")
            raise
        for d in devices:
            self.remember(d.address, d.kind, d.panel)
        self._emit("scan_finished", devices=devices)
        return devices

    async def _connect(self, address: str):
        lock = self._cmd_locks.setdefault(address, asyncio.Lock())
        async with lock:
            existing = self._clients.get(address)
            if existing is not None and self._is_alive(existing):
                return existing.get_device_info()
            if existing is not None:  # stale after an unexpected drop
                await self._safe_disconnect(existing)
            self._emit("connecting", address=address)
            client = self._client_factory(address)
            try:
                await client.connect()
            except Exception as e:
                self._clients.pop(address, None)
                self._emit("connect_failed", address=address, message=str(e))
                raise
            self._clients[address] = client
            info = client.get_device_info()
            self._emit("connected", address=address, info=info)
            return info

    async def _disconnect(self, address: str) -> None:
        self._held.discard(address)
        client = self._clients.pop(address, None)
        if client is not None:
            await self._safe_disconnect(client)
            self._emit("disconnected", address=address, expected=True)

    @staticmethod
    async def _safe_disconnect(client: Any) -> None:
        try:
            await client.disconnect()
        except Exception:
            log.debug("disconnect raised", exc_info=True)

    async def _set_hold(self, address: str, hold: bool) -> None:
        if hold:
            self._held.add(address)
            if not self.is_connected(address):
                await self._connect(address)
        else:
            self._held.discard(address)
        self._emit("hold_changed", address=address, hold=hold)

    async def _send_image_file(self, address: str, path: str, resize_method: str, save_slot: int,
                               budget_kb: float) -> media.PreparedImage:
        client = self._require(address, "send_image")
        info = client.get_device_info()
        self._emit("upload_preparing", address=address, name=str(path))
        budget_kb *= getattr(info, "panels", 1)  # a group gets a budget per panel
        self._shown.pop(address, None)
        try:
            prepared = await asyncio.to_thread(media.prepare, path, info.width, info.height,
                                               resize_method, budget_kb)
        except Exception as e:
            self._emit("command_failed", address=address, command="send_image", message=f"can't read image: {e}")
            raise
        self._emit("upload_prepared", address=address, summary=prepared.describe(),
                   source_kb=prepared.source_bytes / 1024)
        lock = self._cmd_locks.setdefault(address, asyncio.Lock())
        async with lock:
            client = self._require(address, "send_image")
            started = time.monotonic()
            try:
                await self._send_prepared(client, address, prepared, int(save_slot))
            except Exception as e:
                self._emit("upload_finished", address=address, ok=False)
                self._emit("command_failed", address=address, command="send_image",
                           message=_friendly_upload_error(e, self._is_alive(client)))
                raise
        self._emit("upload_finished", address=address, ok=True, seconds=time.monotonic() - started,
                   kb=prepared.kb)
        self._emit("command_ok", address=address, command="send_image", params={})
        return prepared

    async def _send_prepared(self, client: Any, address: str, prepared: media.PreparedImage,
                             save_slot: int) -> None:
        """Upload an already display-sized image without pypixelcolor re-encoding it."""
        def report(i: int, n: int, done: int, total: int) -> None:
            self._emit("upload_progress", address=address, window=i, windows=n, sent=done, total=total)

        if hasattr(type(client), "send_prepared"):  # our own drivers (iLEDColor)
            await client.send_prepared(prepared, save_slot=save_slot, progress=report)
            return
        hex_data = prepared.data.hex()
        session = getattr(client, "_session", None)
        try:
            from pypixelcolor.commands.send_image import send_image_hex
        except ImportError:  # pragma: no cover
            send_image_hex = None
        if session is None or not hasattr(session, "execute_command") or send_image_hex is None:
            # Unknown pypixelcolor internals: fall back to the public API (no progress, re-encodes).
            await client.send_image_hex(hex_string=hex_data, file_extension=prepared.ext, save_slot=save_slot)
            return

        # No ``device_info`` parameter here, so the session can't inject it and
        # pypixelcolor sends our bytes exactly as prepared.
        def command(hex_string: str, file_extension: str, save_slot: int = 0):
            plan = send_image_hex(hex_string, file_extension, device_info=None, save_slot=save_slot)
            plan.windows = _ProgressWindows(list(plan.windows), report)
            return plan

        result = await session.execute_command(command, hex_string=hex_data, file_extension=prepared.ext,
                                               save_slot=save_slot)
        if getattr(result, "success", True) is False:
            raise RuntimeError(getattr(result, "message", "upload failed"))

    def _require(self, address: str, command: str) -> Any:
        client = self._clients.get(address)
        if client is None or not self._is_alive(client):
            err = RuntimeError(f"{address} is not connected")
            self._emit("command_failed", address=address, command=command, message=str(err))
            raise err
        return client

    async def _run_preset(self, address: str, preset, remote: bool, allow_destructive: bool,
                          save_slot: int | None = None, skip_unchanged: bool = False) -> None:
        client = self._clients.get(address)
        if client is None or not self._is_alive(client):
            err = RuntimeError(f"{address} is not connected")
            self._emit("command_failed", address=address, command=preset.name, message=str(err))
            raise err
        info = client.get_device_info()
        try:
            # May draw animations or fetch live data – keep it off the Bluetooth loop.
            steps = await asyncio.to_thread(preset.steps, info.width, info.height)
            digest = _digest(steps)
        except Exception as e:
            self._emit("command_failed", address=address, command=preset.name, message=str(e))
            raise
        if skip_unchanged and self._shown.get(address) == digest:
            self._emit("live_unchanged", address=address, preset=preset.name)
            return
        for command, params in steps:
            if command == "image_file":  # saved image preset: a file in our media folder
                from .config import media_dir
                await self._send_image_file(address, str(media_dir() / Path(params["file"]).name),
                                            params.get("resize_method", "crop"), save_slot or 0,
                                            params.get("budget_kb", media.DEFAULT_BUDGET_KB))
                continue
            if save_slot is not None and command in SLOT_COMMANDS:
                params = {**params, "save_slot": save_slot}
            await self._run(address, command, params, remote, allow_destructive)
        self._shown[address] = digest
        self._emit("preset_ok", address=address, preset=preset.name)

    async def _run(self, address: str, command: str, params: dict | None,
                   remote: bool, allow_destructive: bool):
        try:
            kwargs = validate(command, params, remote=remote, allow_destructive=allow_destructive)
            method, kwargs = to_client_call(command, kwargs)
        except Exception as e:
            self._emit("command_failed", address=address, command=command, message=str(e))
            raise
        if command in CONTENT_COMMANDS:
            self._shown.pop(address, None)  # the display now shows something else
        caps = self.capabilities(address)
        if caps is not None and command not in caps:
            err = RuntimeError(f"this display doesn't support {command}")
            self._emit("command_failed", address=address, command=command, message=str(err))
            raise err
        lock = self._cmd_locks.setdefault(address, asyncio.Lock())
        async with lock:
            client = self._clients.get(address)
            if client is None or not self._is_alive(client):
                err = RuntimeError(f"{address} is not connected")
                self._emit("command_failed", address=address, command=command, message=str(err))
                raise err
            try:
                result = await getattr(client, method)(**kwargs)
            except Exception as e:
                self._emit("command_failed", address=address, command=command, message=str(e))
                raise
        self._emit("command_ok", address=address, command=command, params=kwargs if method == command else {})
        return result

    async def _watchdog(self) -> None:
        """Notice dropped links and reconnect held devices."""
        while True:
            await asyncio.sleep(self._watchdog_interval)
            for address, client in list(self._clients.items()):
                if not self._is_alive(client):
                    self._clients.pop(address, None)
                    self._emit("disconnected", address=address, expected=False)
            for address in list(self._held):
                if address not in self._clients:
                    try:
                        await self._connect(address)
                        self._emit("reconnected", address=address)
                    except Exception:
                        pass  # try again next tick; connect_failed was emitted
