# Running the bridge on a Raspberry Pi (or any small Linux box)

The bridge works like **zigbee2mqtt**: a small always-on computer near the
displays talks to them over its own Bluetooth and to Home Assistant (or Node-RED,
or anything else) over MQTT.

```
 LED displays  ))) Bluetooth )))  Raspberry Pi            ──MQTT──►  Home Assistant
 (within ~10 m)                   pypixelgui-bridge                  (Mosquitto broker)
```

Displays show up in Home Assistant automatically (MQTT discovery): a light with
your presets as effects, a message box, preset and rotation selects, and a
"hold connection" switch. Live presets (weather, departures, schedules) keep
themselves up to date on the Pi, so HA only has to pick one.

## What you need

- A Raspberry Pi with Bluetooth: Pi Zero 2 W, 3, 4 or 5 (or any Linux SBC/mini PC
  with Bluetooth LE). It must be within Bluetooth range of the displays – put it
  close to them, not next to the router.
- An MQTT broker. In Home Assistant: *Settings → Add-ons → Mosquitto broker*, then
  create a user for the bridge (*Settings → People → Users*).

## Install (Raspberry Pi OS Lite)

```bash
sudo apt install -y python3-venv bluez git
sudo useradd --system --create-home --home-dir /var/lib/pypixelgui --groups bluetooth pypixel
sudo python3 -m venv /opt/pypixelgui
sudo /opt/pypixelgui/bin/pip install -r https://raw.githubusercontent.com/Tahtk3v/pypixelgui/main/mqtt/requirements-bridge.txt
sudo /opt/pypixelgui/bin/pip install --no-deps "git+https://github.com/Tahtk3v/pypixelgui.git" "git+https://github.com/Tahtk3v/pypixelgui.git#subdirectory=mqtt"
```

The bridge-only install is about 50 MB and doesn't need the desktop GUI (Qt).

## Set up

Run these as the service user so the settings end up where the service reads them:

```bash
sudo -u pypixel env XDG_CONFIG_HOME=/var/lib/pypixelgui /opt/pypixelgui/bin/pypixelgui-bridge scan
sudo -u pypixel env XDG_CONFIG_HOME=/var/lib/pypixelgui /opt/pypixelgui/bin/pypixelgui-bridge add 1D:6B:5E:B5:A5:54 --name "Desk sign"
sudo -u pypixel env XDG_CONFIG_HOME=/var/lib/pypixelgui /opt/pypixelgui/bin/pypixelgui-bridge mqtt --host homeassistant.local --username pypixel --password '…'
```

Close the phone app first – the displays accept one Bluetooth connection at a
time. Saved displays are *held* by default: the bridge stays connected, which
also stops other phones from taking them over.

Presets, rotations, display groups and live presets you made in the desktop app
work too: copy the settings file (shown by `pypixelgui-bridge list` on each
machine) from your computer to `/var/lib/pypixelgui/pypixelgui/config.json` on
the Pi. Settings are plain JSON.

## Start at boot

```bash
sudo curl -o /etc/systemd/system/pypixelgui-bridge.service https://raw.githubusercontent.com/Tahtk3v/pypixelgui/main/mqtt/deploy/pypixelgui-bridge.service
sudo systemctl enable --now pypixelgui-bridge
journalctl -u pypixelgui-bridge -f
```

## Docker

`mqtt/deploy/Dockerfile` and `docker-compose.yml` run the same bridge in a
container. Bluetooth goes through the host's BlueZ by mounting `/run/dbus`; the
broker can be set with `PYPIXELGUI_MQTT_*` environment variables.

## What about ESP32s?

An ESP32 can't run this (it's Python), but there are two ways ESP32s fit in:

1. **ESPHome Bluetooth proxies – the Home Assistant way.** Flash cheap ESP32s with
   ESPHome's *Bluetooth Proxy* firmware and place them around the house; Home
   Assistant then reaches Bluetooth devices through whichever proxy is nearest.
   To use that, the display control has to run *inside* Home Assistant as a
   custom integration (installed via HACS) instead of as this separate bridge.
   That's a separate piece of work – not built yet.
2. **Firmware that drives the displays itself** (an ESPHome component). The
   upload protocols, GIF/text rendering and live data would all have to be
   rewritten in C++ for a microcontroller; not planned.

Until then, a Pi Zero 2 W next to the displays (≈ €20) running this bridge is the
simplest setup, and works with HA, Node-RED or anything else that speaks MQTT.
