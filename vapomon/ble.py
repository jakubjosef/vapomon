"""Bluetooth I/O for Storz & Bickel vaporizers, built on `bleak`.

`protocol.py` holds the pure decode logic; this module does the talking:
scanning, connecting, a one-shot read, and a continuous watch. Three device
families are supported:

* Crafty / Crafty+ and Volcano expose the temperature as a plain GATT
  characteristic you can just read / subscribe to.
* Venty / Veazy speak a framed command/response protocol over a single
  characteristic: you subscribe, send a short handshake, then poll command
  0x01 and the device answers with a status frame containing the temperature.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, Optional

from bleak import BleakClient, BleakScanner

from . import protocol
from .protocol import Model, Status, VapomonError

# Called with (model, temperature_celsius) for every fresh reading.
TempCallback = Callable[[Model, float], None]

# Venty/Veazy command bytes written to QVAP_CHAR. All are write-with-response,
# matching the app's writeValue() (Web Bluetooth's response-by-default write).
_VENTY_HANDSHAKE = (0x02, 0x1D, 0x01, 0x04)  # QvapConnect3
_VENTY_POLL = 0x01                            # periodicIntervalFunc
_VENTY_POLL_INTERVAL = 0.5                    # seconds (app uses 500 ms)


@dataclass
class Discovered:
    address: str
    name: Optional[str]
    model: Model


async def scan(timeout: float = 8.0, name_filter: Optional[str] = None) -> list[Discovered]:
    """Discover nearby Storz & Bickel devices, newest-API-friendly."""
    results = await BleakScanner.discover(timeout=timeout, return_adv=True)
    found: list[Discovered] = []
    for device, adv in results.values():
        name = adv.local_name or device.name
        model = protocol.detect_model(name)
        if model is None:
            continue
        if name_filter and (not name or name_filter.lower() not in name.lower()):
            continue
        found.append(Discovered(address=device.address, name=name, model=model))
    return found


async def read_once(
    address: str, model: Optional[Model] = None, connect_timeout: float = 20.0
) -> tuple[Model, float]:
    """Connect, read the current temperature once, disconnect."""
    async with BleakClient(address, timeout=connect_timeout) as client:
        resolved = model or _detect_connected(client)
        return resolved, await _read_temp(client, resolved)


async def watch(
    address: str,
    model: Optional[Model],
    on_temp: TempCallback,
    connect_timeout: float = 20.0,
) -> None:
    """Connect and stream temperature readings until cancelled or disconnected."""
    loop = asyncio.get_running_loop()
    disconnected = asyncio.Event()

    def _on_disconnect(_client: BleakClient) -> None:
        loop.call_soon_threadsafe(disconnected.set)

    async with BleakClient(
        address, timeout=connect_timeout, disconnected_callback=_on_disconnect
    ) as client:
        resolved = model or _detect_connected(client)
        if resolved in (Model.VENTY, Model.VEAZY):
            await _watch_venty(client, resolved, on_temp, disconnected)
        else:
            char = _simple_char(resolved)
            await _watch_simple(client, resolved, char, on_temp, disconnected)


# --- model detection on an open connection (when connecting by address) -----

def _detect_connected(client: BleakClient) -> Model:
    uuids = {service.uuid.lower() for service in client.services}
    if protocol.VOLCANO_SERVICE in uuids:
        return Model.VOLCANO
    if protocol.QVAP_SERVICE in uuids:
        return Model.VENTY  # Venty/Veazy share a protocol; can't tell apart by service
    if protocol.CRAFTY_SERVICE in uuids:
        return Model.CRAFTY
    raise VapomonError("connected device is not a recognised Storz & Bickel model")


def _simple_char(model: Model) -> str:
    return protocol.VOLCANO_CURRENT_TEMP if model == Model.VOLCANO else protocol.CRAFTY_CURRENT_TEMP


# --- one-shot reads ---------------------------------------------------------

async def _read_temp(client: BleakClient, model: Model) -> float:
    if model in (Model.VENTY, Model.VEAZY):
        return await _read_venty(client)
    raw = await client.read_gatt_char(_simple_char(model))
    temp = protocol.decode_simple_temp(bytes(raw))
    if temp is None:
        raise VapomonError("device returned no valid temperature")
    return temp


async def _read_venty(client: BleakClient, timeout: float = 12.0) -> float:
    loop = asyncio.get_running_loop()
    result: asyncio.Future[float] = loop.create_future()

    def _on_notify(_handle, data: bytearray) -> None:
        temp = protocol.decode_venty_frame(bytes(data))
        if temp is not None and not result.done():
            result.set_result(temp)

    await client.start_notify(protocol.QVAP_CHAR, _on_notify)
    try:
        for code in _VENTY_HANDSHAKE:
            await client.write_gatt_char(protocol.QVAP_CHAR, _venty_cmd(code), response=True)
        poller = asyncio.ensure_future(_venty_poll(client, result))
        try:
            return await asyncio.wait_for(asyncio.shield(result), timeout)
        except asyncio.TimeoutError:
            raise VapomonError("timed out waiting for a Venty/Veazy temperature frame")
        finally:
            poller.cancel()
    finally:
        await client.stop_notify(protocol.QVAP_CHAR)


# --- continuous watch -------------------------------------------------------

async def _watch_simple(
    client: BleakClient,
    model: Model,
    char: str,
    on_temp: TempCallback,
    disconnected: asyncio.Event,
) -> None:
    def _on_notify(_handle, data: bytearray) -> None:
        temp = protocol.decode_simple_temp(bytes(data))
        if temp is not None:
            on_temp(model, temp)

    # Emit the current value immediately; notifications then fire on change.
    raw = await client.read_gatt_char(char)
    initial = protocol.decode_simple_temp(bytes(raw))
    if initial is not None:
        on_temp(model, initial)

    await client.start_notify(char, _on_notify)
    try:
        await disconnected.wait()
    finally:
        if client.is_connected:
            await client.stop_notify(char)


async def _watch_venty(
    client: BleakClient,
    model: Model,
    on_temp: TempCallback,
    disconnected: asyncio.Event,
) -> None:
    def _on_notify(_handle, data: bytearray) -> None:
        temp = protocol.decode_venty_frame(bytes(data))
        if temp is not None:
            on_temp(model, temp)

    await client.start_notify(protocol.QVAP_CHAR, _on_notify)
    try:
        for code in _VENTY_HANDSHAKE:
            await client.write_gatt_char(protocol.QVAP_CHAR, _venty_cmd(code), response=True)
        poller = asyncio.ensure_future(_venty_poll(client, None))
        try:
            await disconnected.wait()
        finally:
            poller.cancel()
    finally:
        if client.is_connected:
            await client.stop_notify(protocol.QVAP_CHAR)


async def _venty_poll(client: BleakClient, stop_when: Optional[asyncio.Future]) -> None:
    """Poll command 0x01 every 500 ms so the device keeps sending status frames."""
    while stop_when is None or not stop_when.done():
        try:
            await client.write_gatt_char(protocol.QVAP_CHAR, _venty_cmd(_VENTY_POLL), response=True)
        except Exception:
            return
        await asyncio.sleep(_VENTY_POLL_INTERVAL)


def _venty_cmd(code: int, length: int = 20) -> bytes:
    buffer = bytearray(length)
    buffer[0] = code
    return bytes(buffer)


# === full status (temperature + battery + usage + ...) ======================

# Called with a Status snapshot for every update.
StatusCallback = Callable[[Status], None]


async def read_status(
    address: str, model: Optional[Model] = None, connect_timeout: float = 20.0
) -> Status:
    """Connect, read everything the device exposes, disconnect."""
    async with BleakClient(address, timeout=connect_timeout) as client:
        resolved = model or _detect_connected(client)
        return await _read_status_open(client, resolved)


async def watch_status(
    address: str,
    model: Optional[Model],
    on_status: StatusCallback,
    connect_timeout: float = 20.0,
    interval: float = 2.0,
) -> None:
    """Connect and stream full status until cancelled or disconnected."""
    loop = asyncio.get_running_loop()
    disconnected = asyncio.Event()

    def _on_disconnect(_client: BleakClient) -> None:
        loop.call_soon_threadsafe(disconnected.set)

    async with BleakClient(
        address, timeout=connect_timeout, disconnected_callback=_on_disconnect
    ) as client:
        resolved = model or _detect_connected(client)
        if resolved in (Model.VENTY, Model.VEAZY):
            await _watch_status_venty(client, resolved, on_status, disconnected)
        else:
            await _watch_status_simple(client, resolved, on_status, disconnected, interval)


async def _read_status_open(client: BleakClient, model: Model) -> Status:
    if model in (Model.VENTY, Model.VEAZY):
        return await _status_venty(client, model)
    if model == Model.VOLCANO:
        return await _status_volcano(client, model)
    return await _status_crafty(client, model)


# --- per-model status, on an already-connected client -----------------------

async def _status_crafty(client: BleakClient, model: Model) -> Status:
    status = Status(model=model)
    status.current_temp_c = _temp(await _try_read(client, protocol.CRAFTY_CURRENT_TEMP))
    status.target_temp_c = protocol.decode_crafty_target(
        await _try_read(client, protocol.CRAFTY_TARGET_TEMP) or b""
    )
    status.boost_temp_c = _temp(await _try_read(client, protocol.CRAFTY_BOOST_TEMP))
    status.battery_percent = _u16(await _try_read(client, protocol.CRAFTY_BATTERY))
    status.auto_off_seconds = _u16(await _try_read(client, protocol.CRAFTY_AUTO_OFF_LIVE))
    status.usage_hours = _u16(await _try_read(client, protocol.CRAFTY_HOURS))
    status.usage_minutes = _u16(await _try_read(client, protocol.CRAFTY_MINUTES))
    reg = _u16(await _try_read(client, protocol.CRAFTY_STATUS))
    if reg is not None:
        status.heating = bool(reg & protocol.MASK_CRAFTY_HEATING)
    reg2 = _u16(await _try_read(client, protocol.CRAFTY_STATUS2))
    if reg2 is not None:
        status.ready = bool(reg2 & protocol.MASK_CRAFTY_READY)
    status.serial = _text(await _try_read(client, protocol.CRAFTY_SERIAL), limit=8)
    status.firmware = _text(await _try_read(client, protocol.CRAFTY_FIRMWARE))
    return status


async def _status_volcano(client: BleakClient, model: Model) -> Status:
    status = Status(model=model)
    status.current_temp_c = _temp(await _try_read(client, protocol.VOLCANO_CURRENT_TEMP))
    status.target_temp_c = _temp(await _try_read(client, protocol.VOLCANO_TARGET_TEMP))
    status.auto_off_seconds = _u16(await _try_read(client, protocol.VOLCANO_AUTO_OFF_LIVE))
    status.usage_hours = _u16(await _try_read(client, protocol.VOLCANO_HEAT_HOURS))
    status.usage_minutes = _u16(await _try_read(client, protocol.VOLCANO_HEAT_MINUTES))
    reg = _u16(await _try_read(client, protocol.VOLCANO_STATUS1))
    if reg is not None:
        status.heating = bool(reg & protocol.MASK_VOLCANO_HEATING)
        status.pump = bool(reg & protocol.MASK_VOLCANO_PUMP)
    status.serial = _text(await _try_read(client, protocol.VOLCANO_SERIAL), limit=8)
    status.firmware = _text(await _try_read(client, protocol.VOLCANO_FIRMWARE))
    return status


async def _status_venty(client: BleakClient, model: Model) -> Status:
    serial = await _venty_serial(client)
    fields = await _venty_status_fields(client)
    return Status(model=model, serial=serial, **fields)


async def _venty_status_fields(client: BleakClient) -> dict:
    """Run the Venty handshake/poll and return one decoded status frame as kwargs."""
    loop = asyncio.get_running_loop()
    result: asyncio.Future[dict] = loop.create_future()

    def _on_notify(_handle, data: bytearray) -> None:
        decoded = protocol.decode_venty_status(bytes(data))
        if decoded is not None and not result.done():
            result.set_result(decoded)

    await client.start_notify(protocol.QVAP_CHAR, _on_notify)
    try:
        for code in _VENTY_HANDSHAKE:
            await client.write_gatt_char(protocol.QVAP_CHAR, _venty_cmd(code), response=True)
        poller = asyncio.ensure_future(_venty_poll(client, result))
        try:
            return await asyncio.wait_for(asyncio.shield(result), 12.0)
        except asyncio.TimeoutError:
            raise VapomonError("timed out waiting for a Venty/Veazy status frame")
        finally:
            poller.cancel()
    finally:
        await client.stop_notify(protocol.QVAP_CHAR)


async def _venty_serial(client: BleakClient) -> Optional[str]:
    name = _text(await _try_read(client, protocol.GAP_DEVICE_NAME))
    if name and " " in name:
        return name.split(" ", 1)[1]
    return name


# --- continuous status watch ------------------------------------------------

async def _watch_status_simple(
    client: BleakClient,
    model: Model,
    on_status: StatusCallback,
    disconnected: asyncio.Event,
    interval: float,
) -> None:
    while client.is_connected and not disconnected.is_set():
        on_status(await _read_status_open(client, model))
        try:
            await asyncio.wait_for(disconnected.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def _watch_status_venty(
    client: BleakClient,
    model: Model,
    on_status: StatusCallback,
    disconnected: asyncio.Event,
) -> None:
    serial = await _venty_serial(client)

    def _on_notify(_handle, data: bytearray) -> None:
        decoded = protocol.decode_venty_status(bytes(data))
        if decoded is not None:
            on_status(Status(model=model, serial=serial, **decoded))

    await client.start_notify(protocol.QVAP_CHAR, _on_notify)
    try:
        for code in _VENTY_HANDSHAKE:
            await client.write_gatt_char(protocol.QVAP_CHAR, _venty_cmd(code), response=True)
        poller = asyncio.ensure_future(_venty_poll(client, None))
        try:
            await disconnected.wait()
        finally:
            poller.cancel()
    finally:
        if client.is_connected:
            await client.stop_notify(protocol.QVAP_CHAR)


# --- small read helpers (return None instead of raising) --------------------

async def _try_read(client: BleakClient, char: str) -> Optional[bytes]:
    try:
        return bytes(await client.read_gatt_char(char))
    except Exception:
        return None


def _temp(raw: Optional[bytes]) -> Optional[float]:
    return protocol.decode_simple_temp(raw) if raw else None


def _u16(raw: Optional[bytes]) -> Optional[int]:
    return protocol.decode_u16(raw) if raw else None


def _text(raw: Optional[bytes], limit: Optional[int] = None) -> Optional[str]:
    if not raw:
        return None
    text = raw.decode("utf-8", "replace").replace("\x00", "").strip()
    if not text:
        return None
    return text[:limit] if limit else text
