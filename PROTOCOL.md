# Storz & Bickel BLE protocol (reverse-engineered)

Everything here was extracted from the official **Web App**,
<https://app.storz-bickel.com> (v3.4.1), which drives the devices over the
Web Bluetooth API. The relevant source files are `js/main.js`, `js/crafty.js`,
`js/qvap.js`, and `js/volcano.js`.

## Discovery & model detection

The app scans for devices whose advertised name starts with one of:
`STORZ&BICKEL`, `Storz&Bickel`, `S&B` (`main.js`, `onButtonClick`).

Model is then determined from the name (`main.js`, `whichDeviceConnected`):

| Advertised name contains | Model          | App module   |
|--------------------------|----------------|--------------|
| `S&B VOLCANO`            | Volcano Hybrid | `volcano.js` |
| `S&B VY`                 | Venty          | `qvap.js`    |
| `S&B VZ`                 | Veazy          | `qvap.js`    |
| (otherwise)              | Crafty/Crafty+ | `crafty.js`  |

The UUID suffixes encode the vendor name in ASCII:
`…-5354-4f52-5a26-4249434b454c` = `"STORZ&BICKEL"`, and the Crafty suffix
`…-4c45-4b43-4942-265a524f5453` is the same string reversed.

## Value encoding

All multi-byte values are **little-endian**. The app's helper
`convertBLEtoUint16(buf) = buf[0] + buf[1] * 256`. Temperatures are transmitted
in **tenths of a degree Celsius** (e.g. `0x07AE` = 1966 → 196.6 °C).

A value of `0xFFFF` means "no valid reading".

## Crafty / Crafty+ (`crafty.js`)

Service `00000001-4c45-4b43-4942-265a524f5453`:

| Characteristic                         | Meaning                  | Access        | Decode      |
|----------------------------------------|--------------------------|---------------|-------------|
| `00000011-4c45-4b43-4942-265a524f5453` | **Current temperature**  | read + notify | `u16le / 10`|
| `00000021-4c45-4b43-4942-265a524f5453` | Target (set) temp        | read + write  | `u16le / 10`|
| `00000031-4c45-4b43-4942-265a524f5453` | Boost temp               | read + write  | `u16le / 10`|
| `00000041-4c45-4b43-4942-265a524f5453` | Battery %                | read + notify | `u16le`     |
| `00000051-4c45-4b43-4942-265a524f5453` | LED brightness           | read + write  | `u16le`     |
| `00000061-4c45-4b43-4942-265a524f5453` | Auto-off setting (s)     | read + write  | `u16le`     |
| `00000071-4c45-4b43-4942-265a524f5453` | Auto-off countdown (s)   | read + notify | `u16le`     |
| `00000081` / `00000091`                | Heater on / off          | write         | —           |

Service `00000002-…`: `00000052` = serial (UTF-8, first 8), `00000032` = firmware (UTF-8),
`00000072` = BLE firmware (bytes → `V{b0}.{b1}.{b2}`).

Service `00000003-…` (status & usage):

| Characteristic                         | Meaning                  | Decode / bits                       |
|----------------------------------------|--------------------------|-------------------------------------|
| `00000023-4c45-4b43-4942-265a524f5453` | Hours of use             | `u16le`                             |
| `000001e3-4c45-4b43-4942-265a524f5453` | Minutes of use           | `u16le`                             |
| `00000093-4c45-4b43-4942-265a524f5453` | Project status reg       | `u16le`; heating = bit 4 (`1<<4`)   |
| `000001c3-4c45-4b43-4942-265a524f5453` | Project status reg 2     | `u16le`; set-temp-reached = bit 2   |

> Note: target temp `> 210` is interpreted by the app as Fahrenheit and
> converted; current temperature is always plain `u16le / 10`.

## Volcano Hybrid (`volcano.js`)

Service `10110000-5354-4f52-5a26-4249434b454c`:

| Characteristic                         | Meaning                | Access        | Decode      |
|----------------------------------------|------------------------|---------------|-------------|
| `10110001-5354-4f52-5a26-4249434b454c` | **Current temperature**| read + notify | `u16le / 10`|
| `10110003-5354-4f52-5a26-4249434b454c` | Target (set) temp      | read + notify | `u16le / 10`|
| `1011000c-5354-4f52-5a26-4249434b454c` | Auto-off countdown (s) | read + notify | `u16le`     |
| `10110015-5354-4f52-5a26-4249434b454c` | Heating hours          | read + notify | `u16le`     |
| `10110016-5354-4f52-5a26-4249434b454c` | Heating minutes        | read + notify | `u16le`     |
| `1011000f-…` / `10110010-…`            | Heater on / off        | write         | —           |
| `10110013-…` / `10110014-…`            | Pump on / off          | write         | —           |

Status register `1010000c-5354-4f52-5a26-4249434b454c` (`u16le`):
heating = bit `32` (`HEIZUNG_ENA`), pump = bit `8192` (`PUMPE_FET_ENABLE`).

Service `10100000-…`: `10100008` = serial, `10100003` = firmware version.
(The Volcano also has legacy services `00000001-1989-0108-1234-123456789abc`
and `01000002-1989-…` used for the UART bootloader / firmware update.)

## Venty / Veazy (`qvap.js`) — framed command/response

These devices use a **single** characteristic for everything:

* Service `00000000-5354-4f52-5a26-4249434b454c`
* Characteristic `00000001-5354-4f52-5a26-4249434b454c` (read / write / notify)

### Connect handshake (`QvapConnect` / `QvapConnect3`)

1. `startNotifications()` and listen for `characteristicvaluechanged`.
2. Write these command frames (20-byte buffer, `buf[0]` = command code):
   `0x02`, `0x1D`, `0x01`, `0x04`. All are write-with-response.
3. Poll status by writing command `0x01` every **500 ms**
   (`periodicIntervalFunc`); roughly every 31st poll it sends `0x04` instead.

### Status frame (`parseBLEResponse`)

`switch(frame[0])`:

* `frame[0] == 0x01` (response to command `0x01`) **and** `len(frame) >= 15`:
  this is the live status frame, with this byte layout (little-endian):

  | bytes   | field                                            |
  |---------|--------------------------------------------------|
  | `[2:4]` | **current temperature** `/ 10` (°C)              |
  | `[4:6]` | target (set) temperature `/ 10` (°C)             |
  | `[6]`   | boost temperature (°C)                           |
  | `[7]`   | superboost temperature (°C)                      |
  | `[8]`   | battery %                                        |
  | `[9]+[10]` | auto-off countdown (seconds)                  |
  | `[11]`  | heater mode: 0 off, 1 on, 2 boost, 3 superboost  |
  | `[13]`  | charger connected (bool)                         |
  | `[14]`  | settings bitfield (bit 0 = °F, bit 1 = ready)    |
  | `[16]`  | settings 2 (bit 0 = BLE permanent), if `len>=17` |

* `frame[0] == 0x00`: analysis/crypto response (lifetime usage stats live here,
  behind a key exchange — not yet decoded by vapomon).
* `frame[0] == 0x30` (48): bootloader-mode frame — ignored for temperature.

The serial number comes from the GAP **Device Name** characteristic
`00002a00-…` (format `"S&B VY <serial>"`); firmware is in the command-`0x02`
response (`frame[2:8]`, UTF-8).

vapomon implements exactly this: subscribe, send the handshake
(`0x02, 0x1D, 0x01, 0x04`), poll `0x01` every 500 ms, and decode the fields
above from frames where `frame[0] == 1`.
