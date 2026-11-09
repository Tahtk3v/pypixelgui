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
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from .commands import to_client_call, validate

#: Commands that can store their content in a device slot.
SLOT_COMMANDS = frozenset({"send_text", "send_image", "send_image_b64"})

log = logging.getLogger(__name__)

Listener = Callable[[str, dict], None]


@dataclass
class FoundDevice:
    name: str
    address: str
    rssi: Optional[int] = None


def _default_client_factory(address: str):
    from pypixelcolor import AsyncClient
    return AsyncClient(address)


async def _default_scan(timeout: float) -> list[FoundDevice]:
    from bleak import BleakScanner
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    out = []
    for dev, adv in found.values():
        name = dev.name or adv.local_name or ""
        if "LED" in name:  # same filter pypixelcolor uses
            out.append(FoundDevice(name=name, address=dev.address, rssi=adv.rssi))
    return sorted(out, key=lambda d: -(d.rssi or -999))


class DeviceManager:
    """Owns BLE clients; every public method is thread-safe and returns a Future."""

    def __init__(self, client_factory: Callable[[str], Any] = _default_client_factory,
                 scan_func: Callable[[float], Awaitable[list[FoundDevice]]] = _default_scan,
                 watchdog_interval: float = 5.0):
        self._client_factory = client_factory
        self._scan_func = scan_func
        self._watchdog_interval = watchdog_interval
        self._clients: dict[str, Any] = {}
        self._held: set[str] = set()
        self._rotations: dict[str, concurrent.futures.Future] = {}
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
        alive = getattr(session, "is_connected", None)
        return bool(alive) if alive is not None else bool(getattr(client, "_connected", True))

    # --- public API (thread-safe) ----------------------------------------

    def scan(self, timeout: float = 5.0) -> concurrent.futures.Future:
        return self.submit(self._scan(timeout))

    def connect(self, address: str) -> concurrent.futures.Future:
        return self.submit(self._connect(address))

    def disconnect(self, address: str) -> concurrent.futures.Future:
        return self.submit(self._disconnect(address))

    def run(self, address: str, command: str, params: dict | None = None, *,
            remote: bool = False, allow_destructive: bool = True) -> concurrent.futures.Future:
        """Validate and run a command. Validation errors surface on the Future."""
        return self.submit(self._run(address, command, params, remote, allow_destructive))

    def run_steps(self, address: str, steps: list[tuple[str, dict]], *, remote: bool = False,
                  allow_destructive: bool = True) -> concurrent.futures.Future:
        """Run several commands in order, stopping at the first failure (used by presets)."""
        async def _all():
            for command, params in steps:
                await self._run(address, command, params, remote, allow_destructive)
        return self.submit(_all())

    def run_preset(self, address: str, preset, *, remote: bool = False,
                   allow_destructive: bool = True) -> concurrent.futures.Future:
        """Render a :class:`~pypixelgui.presets.Preset` for this display's size and run it."""
        return self.submit(self._run_preset(address, preset, remote, allow_destructive))

    # --- rotation ---------------------------------------------------------

    def start_rotation(self, address: str, items: list[tuple[Any, float]], *, loop: bool = True,
                       preload_slot: int | None = None, remote: bool = False,
                       allow_destructive: bool = True) -> concurrent.futures.Future:
        """Cycle through ``(preset, seconds)`` items on one display.

        Replaces any rotation already running there. With ``preload_slot`` set,
        each item is first saved to the display's slots ``preload_slot``,
        ``preload_slot + 1``, … and the rotation then only sends ``show_slot``,
        which switches instantly instead of re-uploading every time.
        """
        if not items:
            raise ValueError("rotation is empty")
        self.stop_rotation(address)
        fut = self.submit(self._rotation(address, list(items), loop, preload_slot, remote, allow_destructive))
        self._rotations[address] = fut
        fut.add_done_callback(lambda f, a=address: self._rotation_done(a, f))
        return fut

    def stop_rotation(self, address: str) -> bool:
        fut = self._rotations.pop(address, None)
        if fut is None:
            return False
        fut.cancel()
        return True

    def rotation_running(self, address: str) -> bool:
        fut = self._rotations.get(address)
        return fut is not None and not fut.done()

    def _rotation_done(self, address: str, fut: concurrent.futures.Future) -> None:
        if self._rotations.get(address) is fut:
            del self._rotations[address]
        self._emit("rotation_stopped", address=address,
                   message=None if fut.cancelled() or fut.exception() is None else str(fut.exception()))

    async def _rotation(self, address: str, items: list, loop: bool, preload_slot: int | None,
                        remote: bool, allow_destructive: bool) -> None:
        total = len(items)
        slots: list[int] = []
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
                        await self._run_preset(address, preset, remote, allow_destructive)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Usually a dropped link; with hold on it comes back, so keep going.
                    await asyncio.sleep(min(secs, 3))
                    continue
                await asyncio.sleep(max(0.5, float(secs)))
            if not loop:
                return

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

    async def _run_preset(self, address: str, preset, remote: bool, allow_destructive: bool,
                          save_slot: int | None = None) -> None:
        client = self._clients.get(address)
        if client is None or not self._is_alive(client):
            err = RuntimeError(f"{address} is not connected")
            self._emit("command_failed", address=address, command=preset.name, message=str(err))
            raise err
        info = client.get_device_info()
        try:
            steps = preset.steps(info.width, info.height)
        except Exception as e:
            self._emit("command_failed", address=address, command=preset.name, message=str(e))
            raise
        for command, params in steps:
            if save_slot is not None and command in SLOT_COMMANDS:
                params = {**params, "save_slot": save_slot}
            await self._run(address, command, params, remote, allow_destructive)
        self._emit("preset_ok", address=address, preset=preset.name)

    async def _run(self, address: str, command: str, params: dict | None,
                   remote: bool, allow_destructive: bool):
        try:
            kwargs = validate(command, params, remote=remote, allow_destructive=allow_destructive)
            method, kwargs = to_client_call(command, kwargs)
        except Exception as e:
            self._emit("command_failed", address=address, command=command, message=str(e))
            raise
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
