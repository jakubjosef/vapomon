# vapomon

A small command-line tool that reads the current temperature from a
**Storz & Bickel** Bluetooth vaporizer — Crafty, Crafty+, Venty, Veazy, or
Volcano Hybrid — without the official app.

The BLE protocol was reverse-engineered from the vendor's own Web App
(<https://app.storz-bickel.com>), which talks to the devices over Web Bluetooth.
See [PROTOCOL.md](PROTOCOL.md) for the full mapping.

## Install

Requires Python 3.9+ and [`bleak`](https://github.com/hbldh/bleak).

```bash
cd vapomon
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

This installs a `vapomon` command. You can also run it without installing:
`python -m vapomon`.

### macOS Bluetooth permission

On the first run, macOS asks the terminal app (Terminal, iTerm, VS Code, …) for
Bluetooth access. If scanning finds nothing, grant it under
**System Settings → Privacy & Security → Bluetooth**, then try again.

## Usage

```bash
# Turn the device on, then:
vapomon                 # scan, connect, print the current temperature once
vapomon --watch         # keep printing as the temperature changes (Ctrl-C to stop)
vapomon --status        # full status: battery, usage timer, auto-off, target, ...
vapomon --status --watch # live dashboard of the full status
vapomon -F              # report in Fahrenheit
vapomon --json          # machine-readable output (works with --status too)
vapomon --scan          # just list nearby S&B devices and exit

# Skip scanning by targeting a specific peripheral (macOS uses a CoreBluetooth
# UUID, not a MAC address — get it from `vapomon --scan`):
vapomon --address 0badf00d-1234-5678-9abc-def012345678 --watch
```

Examples:

```
$ vapomon
Venty: 184.6 °C

$ vapomon --status
Venty  (VY1234)
  Current:     184.6 °C
  Target:      190.0 °C
  Boost:       15.0 °C
  Battery:     87 %
  Charging:    no
  Auto-off in: 1 m 48 s
  Heating:     boost
  Ready:       no

$ vapomon --json
{"model": "Venty", "address": "0BADF00D-...", "temperature_c": 184.6, "temperature_f": 364.3}
```

### What `--status` shows per model

| Field             | Crafty/Crafty+ | Venty/Veazy | Volcano |
|-------------------|:--------------:|:-----------:|:-------:|
| Current / target  | ✅ | ✅ | ✅ |
| Boost / superboost| boost | ✅ | — |
| Battery %         | ✅ | ✅ | — (mains) |
| Auto-off countdown| ✅ | ✅ | ✅ |
| Usage hours       | ✅ | — *(needs analysis cmd)* | ✅ |
| Heating / ready   | ✅ | ✅ | heating + pump |
| Charging          | — | ✅ | — |
| Serial / firmware | ✅ | serial | ✅ |

Fields that a given device or firmware doesn't expose are simply omitted.

## How it works

* **Crafty / Crafty+** and **Volcano Hybrid** expose the current temperature as
  a plain GATT characteristic (`uint16` little-endian, in tenths of a degree).
  vapomon reads it directly, or subscribes for `--watch`.
* **Venty / Veazy** use a framed command/response protocol over a single
  characteristic: vapomon subscribes, sends a short handshake, then polls a
  status command; the device replies with a frame carrying the temperature.

vapomon auto-detects the model from the device's advertised BLE name.

## Develop / test

The decode logic lives in `vapomon/protocol.py` and is pure (no hardware,
no dependencies), so it is unit-tested:

```bash
pip install -e ".[dev]"
pytest
```

## Notes

This is an unofficial, interoperability tool for hardware you own. It is not
affiliated with or endorsed by Storz & Bickel.
