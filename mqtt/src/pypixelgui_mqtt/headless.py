"""Run the MQTT bridge without a GUI (e.g. on a Raspberry Pi next to the display).

    pypixelgui-bridge                # uses the GUI's saved config
    pypixelgui-bridge --config my.json
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from pathlib import Path

from pypixelgui.compat import ensure_ca_certificates
from pypixelgui.config import Settings, default_path
from pypixelgui.device_manager import DeviceManager

from .bridge import MqttBridge


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=default_path(), help="settings file (default: %(default)s)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    ensure_ca_certificates()
    settings = Settings.load(args.config)
    if not settings.devices:
        ap.error(f"no devices in {args.config}; add one with the GUI first")

    manager = DeviceManager()
    manager.add_listener(lambda ev, data: logging.info("%s %s", ev, data))
    for dev in settings.devices:
        # Headless mode always holds: the point is to own the displays.
        manager.set_hold(dev.address, True)

    bridge = MqttBridge(manager, settings, on_status=lambda s: logging.info("MQTT: %s", s))
    bridge.start()

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    stop.wait()
    bridge.stop()
    manager.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
