"""Run the MQTT bridge without a GUI – e.g. on a Raspberry Pi near the displays,
the way zigbee2mqtt runs next to a Zigbee stick. Set it up from a terminal:

    pypixelgui-bridge scan                       # find displays in Bluetooth range
    pypixelgui-bridge add 1D:6B:5E:B5:A5:54 --name "Desk sign"
    pypixelgui-bridge mqtt --host homeassistant.local --username mqtt --password ...
    pypixelgui-bridge list
    pypixelgui-bridge                            # run (same as "run")

The settings file is shared with the desktop GUI, so you can also set things up
there and copy the file over. For containers, MQTT settings can come from
environment variables instead: PYPIXELGUI_MQTT_HOST, _PORT, _USERNAME,
_PASSWORD, _TLS (1/0), _BASE_TOPIC, _HA_DISCOVERY (1/0).
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from pathlib import Path

from pypixelgui.compat import ensure_ca_certificates
from pypixelgui.config import DeviceEntry, Settings, default_path

from .bridge import MqttBridge, MqttSettings

ENV_PREFIX = "PYPIXELGUI_MQTT_"


def _env_overrides(cfg: MqttSettings, environ=os.environ) -> MqttSettings:
    def flag(v: str) -> bool:
        return v.strip().lower() in ("1", "true", "yes", "on")
    conv = {"HOST": ("host", str), "PORT": ("port", int), "USERNAME": ("username", str),
            "PASSWORD": ("password", str), "TLS": ("tls", flag), "CA_CERTS": ("ca_certs", str),
            "BASE_TOPIC": ("base_topic", str), "CLIENT_ID": ("client_id", str),
            "HA_DISCOVERY": ("ha_discovery", flag), "HA_PREFIX": ("ha_prefix", str)}
    for key, (attr, fn) in conv.items():
        value = environ.get(ENV_PREFIX + key)
        if value not in (None, ""):
            setattr(cfg, attr, fn(value))
    return cfg


# --- subcommands -------------------------------------------------------------------

def cmd_scan(args, settings: Settings) -> int:
    from pypixelgui.device_manager import DeviceManager
    manager = DeviceManager()
    try:
        found = manager.scan(args.seconds).result(args.seconds + 15)
    finally:
        manager.shutdown()
    if not found:
        print("No displays found. Is Bluetooth on, and is the phone app disconnected from them?")
        return 1
    print(f"{'NAME':24} {'ADDRESS':38} {'TYPE':10} SIZE   SAVED")
    for d in found:
        size = f"{d.panel['width']}x{d.panel['height']}" if d.panel else "?"
        saved = settings.find(d.address)
        print(f"{d.name[:24]:24} {d.address:38} {d.kind:10} {size:6} {saved.alias if saved else ''}")
    return 0


def cmd_add(args, settings: Settings) -> int:
    from pypixelgui.device_manager import DeviceManager
    kind, panel, name = args.kind, None, args.name or ""
    manager = DeviceManager()
    try:  # a quick scan tells us the type and (iLEDColor) the panel size
        for d in manager.scan(args.seconds).result(args.seconds + 15):
            if d.address.lower() == args.address.lower():
                kind, panel, name = d.kind, d.panel, name or d.name
                break
        else:
            print(f"{args.address} wasn't seen in the scan; saving it anyway as {kind}.")
    finally:
        manager.shutdown()
    if kind == "iledcolor" and not panel:
        print("iLEDColor displays need a scan to learn their size – make sure it's in range and try again.")
        return 1
    entry = settings.upsert(DeviceEntry(address=args.address, name=name, kind=kind, panel=panel,
                                        hold=not args.no_hold))
    entry.kind, entry.panel, entry.hold = kind, panel or entry.panel, not args.no_hold
    if args.password:
        entry.password = args.password
    settings.save(args.config)
    print(f"Saved {name or args.address} as “{entry.alias}” (MQTT topic {args.base or 'pypixel'}/{entry.alias}).")
    return 0


def cmd_remove(args, settings: Settings) -> int:
    before = len(settings.devices)
    settings.devices = [d for d in settings.devices
                        if args.display not in (d.alias, d.address) and args.display.lower() != d.address.lower()]
    if len(settings.devices) == before:
        print(f"No saved display called {args.display}")
        return 1
    settings.save(args.config)
    print(f"Removed {args.display}")
    return 0


def cmd_list(args, settings: Settings) -> int:
    cfg = MqttSettings.from_settings(settings)
    print(f"Settings: {args.config}")
    print(f"MQTT: {cfg.username + '@' if cfg.username else ''}{cfg.host}:{cfg.port} base topic "
          f"“{cfg.base_topic}”, Home Assistant discovery {'on' if cfg.ha_discovery else 'off'}")
    if not settings.devices:
        print("No displays saved yet – run “pypixelgui-bridge scan”, then “add”.")
    for d in settings.devices:
        size = f" {d.panel['width']}x{d.panel['height']}" if d.panel else ""
        print(f"  {d.alias:20} {d.address:38} {d.kind}{size}{'  (held)' if d.hold else ''}")
    for g in settings.groups:
        print(f"  {g['alias']:20} group {g['cols']}x{g['rows']}: {', '.join(g.get('members', []))}")
    print(f"Presets: {len(settings.presets)} saved, rotations: {len(settings.rotations)}")
    return 0


def cmd_mqtt(args, settings: Settings) -> int:
    cfg = MqttSettings.from_settings(settings)
    for attr in ("host", "port", "username", "password", "base_topic", "ca_certs"):
        value = getattr(args, attr)
        if value is not None:
            setattr(cfg, attr, value)
    if args.tls is not None:
        cfg.tls = args.tls
    if args.ha_discovery is not None:
        cfg.ha_discovery = args.ha_discovery
    cfg.enabled = True
    cfg.store(settings)
    settings.save(args.config)
    print(f"MQTT set to {cfg.host}:{cfg.port}{' (TLS)' if cfg.tls else ''}; discovery "
          f"{'on' if cfg.ha_discovery else 'off'}.")
    return 0


def cmd_run(args, settings: Settings) -> int:
    from pypixelgui.device_manager import DeviceManager
    from pypixelgui.groups import GroupSpec
    from pypixelgui.live import ns
    ns.set_api_key(settings.api_keys.get("ns", ""))
    _env_overrides(MqttSettings.from_settings(settings)).store(settings)  # env wins, not saved
    if not settings.devices:
        print(f"No displays in {args.config}. Run “pypixelgui-bridge scan” and “pypixelgui-bridge add …” first.",
              file=sys.stderr)
        return 2

    manager = DeviceManager()
    manager.add_listener(lambda ev, data: logging.info("%s %s", ev, data))
    for g in settings.groups:
        spec = GroupSpec.from_dict(g)
        manager.set_group(spec)
        if spec.hold:
            manager.set_hold(spec.address, True)
    for dev in settings.devices:
        manager.remember(dev.address, dev.kind, dev.panel, dev.password)
        if dev.hold:  # keeps the display connected so nobody else can take it
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


def _bool_flag(p, name: str, help_: str) -> None:
    g = p.add_mutually_exclusive_group()
    g.add_argument(f"--{name}", dest=name.replace("-", "_"), action="store_true", default=None, help=help_)
    g.add_argument(f"--no-{name}", dest=name.replace("-", "_"), action="store_false")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="pypixelgui-bridge", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=default_path(), help="settings file (default: %(default)s)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="command")
    sub.add_parser("run", help="run the bridge (default)")
    p = sub.add_parser("scan", help="list displays in Bluetooth range")
    p.add_argument("--seconds", type=float, default=8)
    p = sub.add_parser("add", help="save a display so the bridge controls it")
    p.add_argument("address")
    p.add_argument("--name")
    p.add_argument("--kind", choices=["ipixel", "iledcolor"], default="ipixel",
                   help="only used if the display isn't seen during the scan")
    p.add_argument("--password", help="iLEDColor display password, if it has one")
    p.add_argument("--no-hold", action="store_true", help="don't keep it connected all the time")
    p.add_argument("--seconds", type=float, default=8)
    p.add_argument("--base", help=argparse.SUPPRESS)
    p = sub.add_parser("remove", help="forget a saved display")
    p.add_argument("display", help="alias or address")
    sub.add_parser("list", help="show saved displays and MQTT settings")
    p = sub.add_parser("mqtt", help="set the MQTT broker")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--username")
    p.add_argument("--password")
    p.add_argument("--base-topic")
    p.add_argument("--ca-certs")
    _bool_flag(p, "tls", "use TLS")
    _bool_flag(p, "ha-discovery", "publish Home Assistant discovery")
    return ap


COMMANDS = {"run": cmd_run, "scan": cmd_scan, "add": cmd_add, "remove": cmd_remove, "list": cmd_list,
            "mqtt": cmd_mqtt, None: cmd_run}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ensure_ca_certificates()
    settings = Settings.load(args.config)
    return COMMANDS[args.command](args, settings)


if __name__ == "__main__":
    raise SystemExit(main())
