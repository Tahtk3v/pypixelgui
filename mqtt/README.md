# pypixelgui-mqtt

MQTT add-on for [pypixelgui](../README.md): control iPixel Color displays from
Home Assistant, Node-RED, or anything that speaks MQTT.

This folder is a self-contained package (it only depends on `pypixelgui` and
`paho-mqtt`), so it can be moved to its own repository as-is.

## Install

```bash
pip install pypixelgui-mqtt
```

From this repo:

```bash
pip install ./mqtt
```

- **With the GUI**: an **MQTT** tab appears. Enter the broker details and press *Save & start*.
- **Headless** (e.g. a Raspberry Pi next to the display): set things up once in the GUI,
  then run the bridge, which also *holds* every saved display so nobody else can connect:

```bash
pypixelgui-bridge
```

## Home Assistant

With **Home Assistant discovery** on (the default), every saved display and display group
appears in HA as a device – no YAML needed. Point the bridge at the same broker HA uses
(e.g. the Mosquitto add-on) and they show up under *Settings → Devices → MQTT*:

| Entity | What it does |
|---|---|
| Light | on/off, brightness; **effect** = any preset (CYBER, flags, your own, weather, departures) |
| Text "Message" | type a message, it appears on the display |
| Select "Preset" | pick a preset |
| Select "Rotation" | start a saved rotation, or "—" to stop |
| Switch "Hold connection" | keep the display connected so phones can't take over |
| Binary sensor "Bluetooth" | whether the display is connected (diagnostic) |

Displays connect automatically when commanded. Entities become unavailable when the
bridge goes offline (MQTT last will). Discovery is re-sent when HA restarts, and entities
of displays you remove are cleaned up.

Automation example – show the departures preset when you walk in:

```yaml
action: light.turn_on
target: {entity_id: light.desk_sign}
data: {effect: "Vertrek Utrecht, Centraal Station"}
```

## zigbee2mqtt-style topics

Same idea as zigbee2mqtt, for Node-RED and other tools:

- `<base>/<alias>` – state JSON (retained):
  `{"state":"ON","brightness":128,"preset":"CYBER","text":"","rotation":"—","hold":"OFF","connected":true}`
- `<base>/<alias>/set` – JSON with any of `state` (`ON`/`OFF`/`TOGGLE`), `brightness` (0–255),
  `effect` or `preset`, `text` (plus `color`, `bg_color`, `animation`, `speed`), `rotation`, `hold`.

```bash
mosquitto_pub -t pypixel/desk/set -m '{"state":"ON","brightness":200,"effect":"Trans flag"}'
```

## Topics

`<base>` defaults to `pypixel`; `<alias>` is the display's (or display group's) alias from the GUI's Security tab.

| Topic | Payload |
|---|---|
| `<base>/<alias>/set/<command>` | JSON object, or a bare value for the main parameter |
| `<base>/<alias>/set/preset` | preset name, e.g. `CYBER`, `Trans flag`, or one of your saved presets |
| `<base>/<alias>/set/rotation` | name of a rotation saved in the GUI, or `stop` |
| `<base>/<alias>/set/hold` | `ON` / `OFF` |
| `<base>/<alias>/set/connect`, `.../disconnect` | anything |
| `<base>/<alias>/status` | `online` / `offline` (retained) |
| `<base>/<alias>/result` | `{"command": ..., "ok": true/false, "error": ...}` |
| `<base>/bridge/status` | `online` / `offline` (retained, last will) |

Commands: `send_text`, `send_image_b64` (`{"data": "<base64>", "ext": ".png"}`),
`set_brightness`, `set_power`, `set_orientation`, `set_clock_mode`, `set_fun_mode`,
`set_pixel`, `set_scores`, `set_timer`, `set_time`, `show_slot`, and – only with
*Allow clear/delete* – `delete`, `clear`.

```bash
mosquitto_pub -t pypixel/desk/set/preset -m CYBER
```

```bash
mosquitto_pub -t pypixel/desk/set/send_text -m '{"text":"Hi","color":"ff0000","animation":"scroll_left"}'
```

## Security

- Use broker authentication and TLS, and restrict `<base>/#` with broker ACLs –
  anyone who can publish there can drive your displays.
- Only displays saved in the GUI are addressable; local file paths and fonts are refused.
- The MQTT password is stored in the pypixelgui config file (mode 600 on Linux/macOS).
