"""Storz & Bickel BLE protocol — reverse-engineered from the official Web App.

Every UUID and decode rule in this module was extracted from the vendor's
Web App (https://app.storz-bickel.com, v3.4.1), which drives the devices over
the Web Bluetooth API. PROTOCOL.md maps each constant back to the exact
function in the app's JavaScript.

This module is intentionally pure: no I/O and no third-party dependencies, so
the decoding can be unit-tested without any hardware.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class VapomonError(Exception):
    """Raised for expected, user-facing failures (no device, bad reading, ...)."""


class Model(str, Enum):
    CRAFTY = "Crafty"      # Crafty / Crafty+   -> crafty.js
    VENTY = "Venty"        # -> qvap.js
    VEAZY = "Veazy"        # -> qvap.js  (same protocol as Venty)
    VOLCANO = "Volcano"    # Volcano Hybrid     -> volcano.js


@dataclass
class Status:
    """A snapshot of everything vapomon can read from a device.

    Fields are None when the model (or its firmware) doesn't expose them.
    """
    model: Model
    current_temp_c: Optional[float] = None
    target_temp_c: Optional[float] = None
    boost_temp_c: Optional[float] = None
    superboost_temp_c: Optional[float] = None
    battery_percent: Optional[int] = None
    auto_off_seconds: Optional[int] = None   # live countdown to auto-shutoff
    usage_hours: Optional[int] = None        # lifetime hours of use / heating
    usage_minutes: Optional[int] = None
    heating: Optional[bool] = None
    heat_mode: Optional[str] = None          # off / on / boost / superboost (Venty)
    charging: Optional[bool] = None
    ready: Optional[bool] = None             # set temperature reached
    pump: Optional[bool] = None              # Volcano air pump running
    serial: Optional[str] = None
    firmware: Optional[str] = None


# BLE advertised-name prefixes the Web App scans for (main.js, onButtonClick).
NAME_PREFIXES = ("STORZ&BICKEL", "Storz&Bickel", "S&B")

# --- Crafty / Crafty+ : UUID suffix is "STORZ&BICKEL" reversed ---------------
CRAFTY_SERVICE = "00000001-4c45-4b43-4942-265a524f5453"
CRAFTY_CURRENT_TEMP = "00000011-4c45-4b43-4942-265a524f5453"   # read+notify, u16le/10 °C
CRAFTY_TARGET_TEMP = "00000021-4c45-4b43-4942-265a524f5453"    # read+write,  u16le/10 °C
CRAFTY_BOOST_TEMP = "00000031-4c45-4b43-4942-265a524f5453"     # read+write,  u16le/10 °C
CRAFTY_BATTERY = "00000041-4c45-4b43-4942-265a524f5453"        # read+notify, u16le = %
CRAFTY_AUTO_OFF_LIVE = "00000071-4c45-4b43-4942-265a524f5453"  # read+notify, u16le seconds left
CRAFTY_HOURS = "00000023-4c45-4b43-4942-265a524f5453"          # read, u16le hours of use
CRAFTY_MINUTES = "000001e3-4c45-4b43-4942-265a524f5453"        # read, u16le minutes of use
CRAFTY_STATUS = "00000093-4c45-4b43-4942-265a524f5453"         # read+notify, project status reg
CRAFTY_STATUS2 = "000001c3-4c45-4b43-4942-265a524f5453"        # read+notify, project status reg 2
CRAFTY_SERIAL = "00000052-4c45-4b43-4942-265a524f5453"         # read, UTF-8 (first 8 chars)
CRAFTY_FIRMWARE = "00000032-4c45-4b43-4942-265a524f5453"       # read, UTF-8
MASK_CRAFTY_HEATING = 1 << 4       # MASK_PRJSTAT_CRAFTY_ACTIVE
MASK_CRAFTY_READY = 1 << 2         # MASK_PRJSTAT2_SET_TEMP_REACHED

# --- Volcano Hybrid ---------------------------------------------------------
VOLCANO_SERVICE = "10110000-5354-4f52-5a26-4249434b454c"
VOLCANO_CURRENT_TEMP = "10110001-5354-4f52-5a26-4249434b454c"  # read+notify, u16le/10 °C
VOLCANO_TARGET_TEMP = "10110003-5354-4f52-5a26-4249434b454c"   # read+notify, u16le/10 °C
VOLCANO_AUTO_OFF_LIVE = "1011000c-5354-4f52-5a26-4249434b454c" # read+notify, u16le seconds left
VOLCANO_HEAT_HOURS = "10110015-5354-4f52-5a26-4249434b454c"    # read+notify, u16le hours
VOLCANO_HEAT_MINUTES = "10110016-5354-4f52-5a26-4249434b454c"  # read+notify, u16le minutes
VOLCANO_STATUS1 = "1010000c-5354-4f52-5a26-4249434b454c"       # read+notify, project status reg 1
VOLCANO_SERIAL = "10100008-5354-4f52-5a26-4249434b454c"        # read, UTF-8 (first 8 chars)
VOLCANO_FIRMWARE = "10100003-5354-4f52-5a26-4249434b454c"      # read, UTF-8
MASK_VOLCANO_HEATING = 32          # MASK_PRJSTAT1_VOLCANO_HEIZUNG_ENA
MASK_VOLCANO_PUMP = 8192           # MASK_PRJSTAT1_VOLCANO_PUMPE_FET_ENABLE

# --- Venty / Veazy : one framed command/response characteristic -------------
QVAP_SERVICE = "00000000-5354-4f52-5a26-4249434b454c"
QVAP_CHAR = "00000001-5354-4f52-5a26-4249434b454c"            # read/write/notify
GAP_DEVICE_NAME = "00002a00-0000-1000-8000-00805f9b34fb"      # "S&B VY <serial>"
BIT_VENTY_READY = 1 << 1           # BIT_SETTINGS_SETPOINT_REACHED
VENTY_HEAT_MODE = {0: "off", 1: "on", 2: "boost", 3: "superboost"}

# Firmware reports this when no valid reading is available.
_INVALID = 0xFFFF


def detect_model(name: Optional[str]) -> Optional[Model]:
    """Map a BLE advertised name to a Model (mirrors main.js whichDeviceConnected)."""
    if not name:
        return None
    upper = name.upper()
    if not any(upper.startswith(prefix.upper()) for prefix in NAME_PREFIXES):
        return None
    if "VOLCANO" in upper:
        return Model.VOLCANO
    if "S&B VY" in upper:
        return Model.VENTY
    if "S&B VZ" in upper:
        return Model.VEAZY
    return Model.CRAFTY


def u16le(data: bytes, offset: int = 0) -> int:
    """Little-endian uint16, matching the app's convertBLEtoUint16()."""
    return data[offset] | (data[offset + 1] << 8)


def decode_u16(raw: bytes) -> Optional[int]:
    """Plain little-endian uint16 from a characteristic value, or None if too short."""
    if len(raw) < 2:
        return None
    return u16le(raw)


def decode_simple_temp(raw: bytes) -> Optional[float]:
    """Decode a Crafty/Volcano temperature characteristic value into °C.

    The whole 2-byte value is the temperature in tenths of a degree
    (crafty.js / volcano.js: convertBLEtoUint16(value) / 10).
    """
    if len(raw) < 2:
        return None
    value = u16le(raw)
    if value == _INVALID:
        return None
    return value / 10.0


def decode_crafty_target(raw: bytes) -> Optional[float]:
    """Decode the Crafty target temperature into °C.

    crafty.js stores the target in the device's display unit; values above 210
    are Fahrenheit and converted back to Celsius for display.
    """
    temp = decode_simple_temp(raw)
    if temp is None:
        return None
    return round((temp - 32) / 1.8, 1) if temp > 210 else temp


def decode_venty_status(frame: bytes) -> Optional[dict]:
    """Decode a Venty/Veazy live status frame (the response to command 0x01).

    Byte layout from qvap.js parseBLEResponse (case 1, length >= 15),
    little-endian throughout:

        [0]    response id (== 1)
        [2:4]  current temperature  (/10 °C)
        [4:6]  target temperature   (/10 °C)
        [6]    boost temperature    (°C)
        [7]    superboost temperature (°C)
        [8]    battery %
        [9]+[10] auto-shutoff countdown (seconds)
        [11]   heater mode (0 off, 1 on, 2 boost, 3 superboost)
        [13]   charger connected
        [14]   settings bitfield (BIT_VENTY_READY = setpoint reached)

    Returns None for anything that isn't a temperature status frame.
    """
    if len(frame) < 15 or frame[0] != 0x01:
        return None
    settings = frame[14]
    mode = frame[11]
    return {
        "current_temp_c": u16le(frame, 2) / 10.0,
        "target_temp_c": u16le(frame, 4) / 10.0,
        "boost_temp_c": float(frame[6]),
        "superboost_temp_c": float(frame[7]),
        "battery_percent": frame[8],
        "auto_off_seconds": frame[9] + frame[10],
        "heat_mode": VENTY_HEAT_MODE.get(mode, "unknown"),
        "heating": mode > 0,
        "charging": frame[13] != 0,
        "ready": bool(settings & BIT_VENTY_READY),
    }


def decode_venty_frame(frame: bytes) -> Optional[float]:
    """Current temperature (°C) from a Venty/Veazy status frame, or None."""
    status = decode_venty_status(frame)
    return status["current_temp_c"] if status else None
