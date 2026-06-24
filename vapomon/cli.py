"""Command-line interface for vapomon."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Optional

from . import __version__, ble
from .protocol import Model, Status, VapomonError


def _c_to_f(celsius: float) -> float:
    return round(celsius * 1.8 + 32, 1)


def _format_temp(celsius: float, fahrenheit: bool) -> str:
    if fahrenheit:
        return f"{_c_to_f(celsius):.1f} °F"
    return f"{celsius:.1f} °C"


def _emit(args: argparse.Namespace, model: Optional[Model], celsius: float, address: str) -> None:
    if args.json:
        print(json.dumps({
            "model": model.value if model else None,
            "address": address,
            "temperature_c": round(celsius, 1),
            "temperature_f": _c_to_f(celsius),
        }), flush=True)
        return
    label = f"{model.value}: " if (model and not args.no_label) else ""
    print(f"{label}{_format_temp(celsius, args.fahrenheit)}", flush=True)


def _fmt_duration(seconds: int) -> str:
    if seconds >= 60:
        return f"{seconds // 60} m {seconds % 60} s"
    return f"{seconds} s"


def _status_dict(status: Status, fahrenheit: bool) -> dict:
    def temp(value):
        if value is None:
            return None
        return _c_to_f(value) if fahrenheit else round(value, 1)

    fields = {
        "model": status.model.value,
        "unit": "F" if fahrenheit else "C",
        "current_temp": temp(status.current_temp_c),
        "target_temp": temp(status.target_temp_c),
        "boost_temp": temp(status.boost_temp_c),
        "superboost_temp": temp(status.superboost_temp_c),
        "battery_percent": status.battery_percent,
        "auto_off_seconds": status.auto_off_seconds,
        "usage_hours": status.usage_hours,
        "usage_minutes": status.usage_minutes,
        "heating": status.heating,
        "heat_mode": status.heat_mode,
        "charging": status.charging,
        "ready": status.ready,
        "pump": status.pump,
        "serial": status.serial,
        "firmware": status.firmware,
    }
    return {key: value for key, value in fields.items() if value is not None}


def _emit_status(args: argparse.Namespace, status: Status) -> None:
    if args.json:
        print(json.dumps(_status_dict(status, args.fahrenheit)), flush=True)
        return

    rows: list[tuple[str, str]] = []

    def add_temp(label: str, value: Optional[float]) -> None:
        if value is not None:
            rows.append((label, _format_temp(value, args.fahrenheit)))

    add_temp("Current", status.current_temp_c)
    add_temp("Target", status.target_temp_c)
    add_temp("Boost", status.boost_temp_c)
    add_temp("Superboost", status.superboost_temp_c)
    if status.battery_percent is not None:
        rows.append(("Battery", f"{status.battery_percent} %"))
    if status.charging is not None:
        rows.append(("Charging", "yes" if status.charging else "no"))
    if status.auto_off_seconds is not None:
        rows.append(("Auto-off in", _fmt_duration(status.auto_off_seconds)))
    if status.heat_mode is not None:
        rows.append(("Heating", status.heat_mode))
    elif status.heating is not None:
        rows.append(("Heating", "yes" if status.heating else "no"))
    if status.pump is not None:
        rows.append(("Pump", "on" if status.pump else "off"))
    if status.ready is not None:
        rows.append(("Ready", "yes" if status.ready else "no"))
    if status.usage_hours is not None:
        rows.append(("Usage", f"{status.usage_hours} h {status.usage_minutes or 0} m"))
    if status.firmware is not None:
        rows.append(("Firmware", status.firmware))

    header = status.model.value + (f"  ({status.serial})" if status.serial else "")
    width = max((len(label) for label, _ in rows), default=0)
    lines = [header] + [f"  {label + ':':<{width + 1}} {value}" for label, value in rows]
    print("\n".join(lines), flush=True)


async def _run(args: argparse.Namespace) -> int:
    if args.address:
        address, model, name = args.address, None, None
    else:
        found = await ble.scan(timeout=args.timeout, name_filter=args.name)
        if args.scan:
            if not found:
                print("No Storz & Bickel devices found.", file=sys.stderr)
                return 1
            for device in found:
                print(f"{device.address}  {device.model.value:8}  {device.name or ''}")
            return 0
        if not found:
            print(_NOT_FOUND_HELP, file=sys.stderr)
            return 1
        target = found[0]
        address, model, name = target.address, target.model, target.name

    if args.status:
        return await _run_status(args, address, model)

    if args.watch:
        def on_temp(detected: Model, celsius: float) -> None:
            _emit(args, detected, celsius, address)

        try:
            await ble.watch(address, model, on_temp, connect_timeout=args.connect_timeout)
        except asyncio.CancelledError:
            pass
        return 0

    detected, celsius = await ble.read_once(address, model, connect_timeout=args.connect_timeout)
    _emit(args, detected, celsius, address)
    return 0


async def _run_status(args: argparse.Namespace, address: str, model: Optional[Model]) -> int:
    if args.watch:
        def on_status(status: Status) -> None:
            _emit_status(args, status)
            if not args.json:
                print(flush=True)  # blank line between snapshots

        try:
            await ble.watch_status(address, model, on_status, connect_timeout=args.connect_timeout)
        except asyncio.CancelledError:
            pass
        return 0

    status = await ble.read_status(address, model, connect_timeout=args.connect_timeout)
    _emit_status(args, status)
    return 0


_NOT_FOUND_HELP = (
    "No Storz & Bickel device found. Is it powered on and in range?\n"
    "On first run, grant your terminal Bluetooth access under\n"
    "System Settings -> Privacy & Security -> Bluetooth."
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vapomon",
        description="Read the temperature from a Storz & Bickel Bluetooth vaporizer "
                    "(Crafty, Crafty+, Venty, Veazy, Volcano Hybrid).",
    )
    parser.add_argument("-w", "--watch", action="store_true",
                        help="keep printing as values change (Ctrl-C to stop)")
    parser.add_argument("-s", "--status", action="store_true",
                        help="show full status (battery, usage, auto-off timer, ...) "
                             "instead of just the temperature")
    parser.add_argument("-F", "--fahrenheit", action="store_true",
                        help="report in °F instead of °C")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument("--scan", action="store_true",
                        help="list discovered devices and exit (no connection)")
    parser.add_argument("--address", metavar="ADDR",
                        help="connect directly to this peripheral (macOS CoreBluetooth UUID "
                             "or MAC address) instead of scanning")
    parser.add_argument("--name", metavar="SUBSTR",
                        help="only consider devices whose name contains SUBSTR")
    parser.add_argument("--no-label", action="store_true",
                        help="print just the temperature, without the model name")
    parser.add_argument("--timeout", type=float, default=8.0, metavar="SECONDS",
                        help="scan duration (default: 8)")
    parser.add_argument("--connect-timeout", type=float, default=20.0, metavar="SECONDS",
                        help="connection timeout (default: 20)")
    parser.add_argument("--version", action="version", version=f"vapomon {__version__}")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130
    except VapomonError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # surface bleak/OS errors without a traceback
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
