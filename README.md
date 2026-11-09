# pypixelgui

A cross-platform (Windows / macOS / Linux) desktop app for **iPixel Color** LED
matrix displays, built on [pypixelcolor](https://github.com/lucagoc/pypixelcolor).

- **Presets** – one-click looks: `CYBER` (flashing yellow), `ALERT`, `ON AIR`,
  and pride flags (Rainbow, Progress, Trans, Bi, Pan, Lesbian, Non-binary, Ace,
  Intersex) drawn at your display's exact resolution. Save your own from the Text tab.
- **Text** – colour, background, animation, speed, rainbow modes, custom fonts, save to slot.
- **Images & GIFs** with crop/fit and a preview at the display's resolution.
- **Clock**, brightness, power, orientation, slots, scoreboard, timer.
- **Locking displays** – see below.
- **MQTT** – optional add-on in [`mqtt/`](mqtt/README.md).

## Install & run

Needs Python 3.10+ and Bluetooth LE.

```bash
pip install .
```

```bash
pypixelgui
```

(or `python -m pypixelgui`). Click **Scan**, pick your display, **Connect**.

For MQTT as well:

```bash
pip install ./mqtt
```

The GUI picks up the add-on automatically and shows an **MQTT** tab.

### Platform notes

- **Linux**: needs BlueZ; your user must be allowed to use Bluetooth (usually the `bluetooth` group).
- **macOS**: the first scan triggers a Bluetooth permission prompt for your terminal/Python.
  macOS hides real Bluetooth addresses, so devices show up as UUIDs – that's normal.
- **Windows 10/11**: works with the built-in Bluetooth stack.

## Locking displays

These displays have no access control of their own as far as pypixelcolor knows:
anyone nearby with the iPixel app can connect. The app gives you three layers:

1. **Hold connection** (checkbox next to the device list, or *Security → Hold*).
   The app keeps the Bluetooth link open and reconnects within seconds if it
   drops. These displays accept one BLE connection at a time, so while it's held,
   phones and other apps can't connect. Displays marked *Hold* under **Security**
   are grabbed automatically on start-up – run the app (or the headless MQTT bridge)
   on an always-on machine near the display to keep it locked.
2. **Only allow connecting to saved displays** (Security tab) – stops the app
   from being pointed at random displays.
3. **Remote access is allow-listed** – the MQTT add-on can only reach saved
   displays (by alias, never by raw address), cannot read local files or fonts,
   and refuses `clear`/`delete` unless you enable it.

**Device-side password:** the display reports a password flag (shown in the
device info panel), and the official app seems to support a PIN, but that part of
the protocol isn't implemented in pypixelcolor yet. Once someone captures the
BLE packets for setting/entering it (see pypixelcolor's
[BLE logging guide](https://github.com/lucagoc/pypixelcolor/wiki/Tutorials#getting-ble-logs-from-an-android-device)),
it can be added as a command there and exposed here – that would be the only
lock that survives the controlling machine being switched off.

## Layout

```
src/pypixelgui/
  commands.py        command schema + validation shared by GUI and add-ons
  device_manager.py  BLE connections on a background asyncio loop, hold/reconnect
  presets.py         built-in & saved presets, flag rendering
  config.py          JSON settings in the per-user config dir
  gui/               PySide6 window
mqtt/                pypixelgui-mqtt add-on (separate package – can move to its own repo)
```

Add-ons register GUI tabs through the `pypixelgui.tabs` entry point group
(a callable taking the main window and returning a `QWidget`), and keep their
settings under `plugins["<name>"]` in the shared config file.

## Development

```bash
pip install -e ".[dev]" -e ./mqtt
```

```bash
QT_QPA_PLATFORM=offscreen pytest tests mqtt/tests
```

Tests use a fake BLE client, so no hardware is needed.
