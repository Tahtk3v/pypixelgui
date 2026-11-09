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
        async def _go():
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
                await self._run(address, command, params, remote, allow_destructive)
            self._emit("preset_ok", address=address, preset=preset.name)
        return self.submit(_go())

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
