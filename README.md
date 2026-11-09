# pypixelgui

A cross-platform (Windows / macOS / Linux) desktop app for Bluetooth LED displays:

- **iPixel Color** displays, via [pypixelcolor](https://github.com/lucagoc/pypixelcolor)
- **iLEDColor** displays (badges, collars, backpacks, signs – names like `iledcolor-…`
  or `iledeyes-…`), via a built-in driver. Scanning reads each panel's size and colour
  format from its advertisement. Text, images, GIFs, presets, rotations, brightness,
  on/off and the display's password work; iPixel-only features (clock, slots,
  scoreboard, timer, orientation) are greyed out. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
  for the projects the protocol knowledge comes from.

- **Presets** – one-click looks: `CYBER` (whole panel flashes yellow, then the
  text), `Rainbow welcome` (flowing rainbow text), `ALERT`, `ON AIR`, and pride
  flags (Rainbow, Progress, Trans, Bi, Pan, Lesbian, Non-binary, Ace, Intersex),
  all drawn at your display's exact resolution. Save your own from the Text or Image
  tab (**Save as preset…**); they appear at the top of the Presets tab.
- **Rotation** – a playlist of presets, each shown for as many seconds as you like,
  looping. Save rotations by name. Optionally loads everything into the display's
  slots first so switching is instant. Sending anything by hand stops the rotation.
- **Text** – colour, background, animation, speed, rainbow modes, custom fonts, save to slot.
- **Images & GIFs** with crop/fit and a preview at the display's resolution. Before
  uploading, animations are shrunk for the display: resized to its exact size, one
  small shared palette, repeated frames merged, and frames thinned out evenly if the
  file is over the **Max size** (default 150 KB). Big uploads take minutes over
  Bluetooth and make displays drop the connection, so a progress bar shows how far along it is.
- **Clock**, brightness, power, orientation, slots, scoreboard, timer.
- **Live** – show now, keep updated, or save as presets that fetch fresh data every
  time they're shown (so they work in rotations and from Home Assistant):
  - **Weather** – current + forecast for any place.
  - **Departures** – real-time Dutch buses, trams, metro and ferries (OVapi), with the
    vehicle type and platform when known; **trains with platforms** via the NS API
    (needs a free personal key from apiportal.ns.nl).
  - **Event schedules** – what's on now and next at BornHack, eth0, CCC Congress (38C3,
    39C3, 40C3…) and CCCamp, or any event that publishes a frab/pretalx `schedule.xml`,
    with the room and the event's own icon. *Rooms…* picks rooms from the schedule
    (Congress has hundreds); events that haven't started say when they start.
  - Text size and scroll speed (characters per second) adapt to the panel; panels two or
    more rows tall show rows (pages of departures/talks) instead of one scrolling line.
- **Combine displays** – put identical displays side by side or stacked and use them as one
  bigger display (Security → *Combine displays…*). Text, images, presets, live data,
  rotations and MQTT all work on the group; brightness and power go to every panel.
  Animations are restarted on all panels together after uploading (automatic on
  iLEDColor; on iPixel tick *start animations together* and pick a free slot).
- **Locking displays** – see below.
- **MQTT / Home Assistant** – optional add-on in [`mqtt/`](mqtt/README.md): displays and
  groups appear in Home Assistant automatically (light with presets as effects, message
  box, preset/rotation selects), with zigbee2mqtt-style JSON topics for everything else.

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
  If you use the python.org installer, the app falls back to the `certifi` certificate
  bundle so emoji in text can still be downloaded (otherwise they fail with
  `CERTIFICATE_VERIFY_FAILED`).
  macOS hides real Bluetooth addresses, so devices show up as UUIDs – that's normal.
- **Windows 10/11**: works with the built-in Bluetooth stack.

## Data sources

- Weather: [Open-Meteo](https://open-meteo.com/) – free, no key, data licensed CC BY 4.0.
- Departures: [OVapi](https://ovapi.nl/) – free, no key; covers buses, trams, metro and
  ferries of all Dutch operators. The list of stops (~a few MB) is downloaded once and
  cached in the settings folder. Trains: [NS API](https://apiportal.ns.nl/) – free personal
  key (product "Ns-App"); paste it on the Live tab.
- Event schedules: frab XML exports (BornHack: `/bornhack-YYYY/program/frab.xml`,
  eth0: `content.eth0.nl/en/<edition>/public/schedule.xml`, Congress:
  `api.events.ccc.de/congress/YYYY/schedule.xml`, CCCamp: the event hub's export). Icons
  come from the event's website or hub (SVG icons are rendered too) and are cached in the
  settings folder.

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

**Device-side password:** iLEDColor displays can store a 6-character password
(Security tab). Testers found it protects the display's settings but, on their units,
did not block new pictures from being sent, and it travels unencrypted – so it's a
deterrent, not a lock; Hold is what keeps others out. For iPixel displays the
password flag is shown in the device info panel, but that part of the protocol
isn't implemented in pypixelcolor yet. Once someone captures the
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
  render.py          text effects (flash, rainbow, scrolling) drawn as GIFs
  media.py           shrinks images/GIFs for a display before upload
  drivers/           iLEDColor protocol (pure, tested against published vectors) + BLE client
  rotation.py        saved rotations
  groups.py          several displays as one (virtual device "group:<alias>")
  live/              weather (Open-Meteo) and NL departures (OVapi)
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
