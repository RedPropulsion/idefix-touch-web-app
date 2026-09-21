#!/usr/bin/env python3
"""Idefix-side MAVLink command tool for the ObeliCS link.

The sender uses the four LED-specific MAV_CMD values inside the standard
COMMAND_LONG message and waits for a standard COMMAND_ACK.  A custom Python
dialect is therefore not required: only the numeric command IDs must match the
ones generated from ``obelics.xml`` for the ObeliCS firmware.

The ``listen`` action remains available to simulate ObeliCS during local tests.
Servo definitions are temporarily commented out for the LED-only demo.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Iterable

# Must be set before importing pymavlink. It enables MAVLink 2 extension fields,
# including the ACK destination system and component.
os.environ.setdefault("MAVLINK20", "1")

try:
    from pymavlink import mavutil
except ImportError:  # pragma: no cover - exercised only on an unprepared host
    mavutil = None


# Project-specific values defined in obelics.xml.  COMMAND_LONG carries the
# selected value in its uint16_t ``command`` field; param1..param7 stay zero.
MAV_CMD_OBELICS_LED_OFF = 60_000
MAV_CMD_OBELICS_LED_BOUNCE = 60_001
MAV_CMD_OBELICS_LED_SPIN = 60_002
MAV_CMD_OBELICS_LED_BLINK = 60_003

# Servo temporaneamente disabilitati per la demo: conservati per il ripristino.
# MAV_CMD_OBELICS_SERVO_OFF = 60_010
# MAV_CMD_OBELICS_SERVO_WIGGLE = 60_011
# MAV_CMD_OBELICS_SERVO_SWEEP = 60_012
# MAV_CMD_OBELICS_SERVO_HELLO = 60_013

DEFAULT_PORT = 14_550
DEFAULT_LOCAL_PORT = 14_551


# class ServoMode(IntEnum):
#     SERVO_OFF = MAV_CMD_OBELICS_SERVO_OFF
#     SERVO_WIGGLE = MAV_CMD_OBELICS_SERVO_WIGGLE
#     SERVO_SWEEP = MAV_CMD_OBELICS_SERVO_SWEEP
#     SERVO_HELLO = MAV_CMD_OBELICS_SERVO_HELLO


class LedMode(IntEnum):
    LED_OFF = MAV_CMD_OBELICS_LED_OFF
    LED_BOUNCE = MAV_CMD_OBELICS_LED_BOUNCE
    LED_SPIN = MAV_CMD_OBELICS_LED_SPIN
    LED_BLINK = MAV_CMD_OBELICS_LED_BLINK


MODE_TYPES: dict[str, type[IntEnum]] = {
    # "servo": ServoMode,
    "led": LedMode,
}


@dataclass(frozen=True)
class DecodedCommand:
    actuator: str
    mode: IntEnum


def require_pymavlink() -> Any:
    if mavutil is None:
        raise SystemExit(
            "Dipendenza mancante: installa pymavlink con "
            "'python3 -m pip install -r requirements.txt'."
        )
    return mavutil


def parse_mode(actuator: str, value: str) -> IntEnum:
    """Parse friendly names such as ``bounce`` or ``LED_BOUNCE``."""
    enum_type = MODE_TYPES[actuator]
    normalized = value.strip().upper().replace("-", "_")
    normalized = normalized.removeprefix("MAV_CMD_OBELICS_")
    # prefix = "SERVO_" if actuator == "servo" else "LED_"
    prefix = "LED_"
    if normalized in {"NONE", "OFF"}:
        normalized = "OFF"
    if not normalized.startswith(prefix):
        normalized = f"{prefix}{normalized}"
    try:
        return enum_type[normalized]
    except KeyError as exc:
        choices = ", ".join(
            mode.name.removeprefix(prefix).lower() for mode in enum_type
        )
        raise ValueError(
            f"modalita {actuator!r} non valida: {value!r}; usa {choices}"
        ) from exc


def decode_command(command: int) -> DecodedCommand:
    for actuator, enum_type in MODE_TYPES.items():
        try:
            return DecodedCommand(actuator, enum_type(command))
        except ValueError:
            continue
    raise LookupError(f"comando MAVLink non supportato: {command}")


def result_name(result: int) -> str:
    util = require_pymavlink()
    entry = util.mavlink.enums.get("MAV_RESULT", {}).get(result)
    return entry.name if entry else f"MAV_RESULT_{result}"


def open_sender(
    host: str,
    remote_port: int,
    bind_address: str,
    local_port: int,
    system_id: int,
    component_id: int,
) -> Any:
    """Open a UDP sender bound to the fixed port used for ObeliCS ACKs."""
    util = require_pymavlink()
    connection = util.mavlink_connection(
        f"udpout:{host}:{remote_port}",
        source_system=system_id,
        source_component=component_id,
        dialect="common",
    )
    try:
        # pymavlink's udpout socket is initially unbound. Binding it before the
        # first send gives Idefix a stable source/listen port, so the fixed
        # remote endpoint configured in zephyr-mavlink can return COMMAND_ACK
        # packets to this same socket.
        connection.port.bind((bind_address, local_port))
    except OSError as exc:
        connection.close()
        raise ConnectionError(
            f"impossibile usare UDP {bind_address}:{local_port}: {exc}"
        ) from exc
    return connection


def send_heartbeat(connection: Any) -> None:
    util = require_pymavlink()
    connection.mav.heartbeat_send(
        util.mavlink.MAV_TYPE_GCS,
        util.mavlink.MAV_AUTOPILOT_INVALID,
        0,
        0,
        util.mavlink.MAV_STATE_ACTIVE,
    )


def wait_for_ack(
    connection: Any,
    command: int,
    timeout: float,
    own_system_id: int,
    own_component_id: int,
) -> Any | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        ack = connection.recv_match(type="COMMAND_ACK", blocking=True, timeout=remaining)
        if ack is None:
            return None
        if ack.command != command:
            continue
        # Zero means broadcast or a MAVLink 1 peer without target extension fields.
        if getattr(ack, "target_system", 0) not in (0, own_system_id):
            continue
        if getattr(ack, "target_component", 0) not in (0, own_component_id):
            continue
        return ack
    return None


def send_one(
    connection: Any,
    actuator: str,
    mode: IntEnum,
    target_system: int,
    target_component: int,
    own_system_id: int,
    own_component_id: int,
    timeout: float,
    retries: int,
) -> bool:
    command = int(mode.value)
    for attempt in range(retries + 1):
        send_heartbeat(connection)
        connection.mav.command_long_send(
            target_system,
            target_component,
            command,
            attempt,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        )
        print(
            f"TX  {actuator.upper():5s} {mode.name:13s} "
            f"command={command} tentativo={attempt + 1}"
        )
        ack = wait_for_ack(
            connection, command, timeout, own_system_id, own_component_id
        )
        if ack is not None:
            name = result_name(ack.result)
            print(
                f"ACK {name} da system={ack.get_srcSystem()} "
                f"component={ack.get_srcComponent()}"
            )
            return ack.result == require_pymavlink().mavlink.MAV_RESULT_ACCEPTED
        print(f"--- nessun ACK entro {timeout:.1f} s")
    return False


def send_ack(connection: Any, message: Any, result: int) -> None:
    connection.mav.command_ack_send(
        message.command,
        result,
        progress=0,
        result_param2=0,
        target_system=message.get_srcSystem(),
        target_component=message.get_srcComponent(),
    )


def addressed_to_us(message: Any, system_id: int, component_id: int) -> bool:
    return message.target_system in (0, system_id) and message.target_component in (
        0,
        component_id,
    )


def listen(args: argparse.Namespace) -> int:
    util = require_pymavlink()
    connection = util.mavlink_connection(
        f"udpin:{args.bind}:{args.port}",
        source_system=args.system_id,
        source_component=args.component_id,
        dialect="common",
    )
    print(
        f"Idefix in ascolto su udp://{args.bind}:{args.port} "
        f"come system={args.system_id} component={args.component_id}"
    )
    print("Ctrl+C per terminare.")
    accepted = 0
    try:
        while args.count == 0 or accepted < args.count:
            message = connection.recv_match(type="COMMAND_LONG", blocking=True, timeout=1.0)
            if message is None:
                continue
            if not addressed_to_us(message, args.system_id, args.component_id):
                continue
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            try:
                decoded = decode_command(message.command)
            except LookupError as exc:
                print(f"RX  {timestamp} RIFIUTATO: {exc}")
                send_ack(connection, message, util.mavlink.MAV_RESULT_UNSUPPORTED)
                continue

            print(
                f"RX  {timestamp} {decoded.actuator.upper():5s} "
                f"{decoded.mode.name:13s} da system={message.get_srcSystem()} "
                f"component={message.get_srcComponent()} seq={message.get_seq()}"
            )
            send_ack(connection, message, util.mavlink.MAV_RESULT_ACCEPTED)
            accepted += 1
    except KeyboardInterrupt:
        print("\nRicevitore arrestato.")
    finally:
        connection.close()
    return 0


def selected_commands(args: argparse.Namespace) -> Iterable[tuple[str, IntEnum]]:
    if args.action == "test-all":
        for actuator, enum_type in MODE_TYPES.items():
            for mode in enum_type:
                yield actuator, mode
    else:
        yield args.actuator, parse_mode(args.actuator, args.mode)


def send(args: argparse.Namespace) -> int:
    try:
        connection = open_sender(
            args.host,
            args.port,
            args.bind,
            args.local_port,
            args.system_id,
            args.component_id,
        )
    except ConnectionError as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        return 2
    success = True
    try:
        for actuator, mode in selected_commands(args):
            ok = send_one(
                connection,
                actuator,
                mode,
                args.target_system,
                args.target_component,
                args.system_id,
                args.component_id,
                args.timeout,
                args.retries,
            )
            success = ok and success
    except ValueError as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        return 2
    finally:
        connection.close()
    return 0 if success else 1


def interactive(args: argparse.Namespace) -> int:
    try:
        connection = open_sender(
            args.host,
            args.port,
            args.bind,
            args.local_port,
            args.system_id,
            args.component_id,
        )
    except ConnectionError as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        return 2
    # print("Comandi: servo <off|wiggle|sweep|hello>, led <off|bounce|spin|blink>, quit")
    print("Comandi: led <off|bounce|spin|blink>, quit")
    try:
        while True:
            try:
                line = input("obeliCS> ").strip()
            except EOFError:
                break
            if not line:
                continue
            if line.lower() in {"quit", "exit", "q"}:
                break
            parts = line.split()
            if len(parts) != 2 or parts[0].lower() not in MODE_TYPES:
                # print("Formato non valido. Esempio: servo wiggle")
                print("Formato non valido. Esempio: led bounce")
                continue
            actuator = parts[0].lower()
            try:
                mode = parse_mode(actuator, parts[1])
            except ValueError as exc:
                print(f"Errore: {exc}")
                continue
            send_one(
                connection,
                actuator,
                mode,
                args.target_system,
                args.target_component,
                args.system_id,
                args.component_id,
                args.timeout,
                args.retries,
            )
    except KeyboardInterrupt:
        print()
    finally:
        connection.close()
    return 0


def add_sender_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", required=True, help="indirizzo IP Ethernet di ObeliCS")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="porta UDP di ObeliCS"
    )
    parser.add_argument(
        "--bind",
        default="0.0.0.0",
        help="indirizzo locale di Idefix su cui ricevere gli ACK",
    )
    parser.add_argument(
        "--local-port",
        type=int,
        default=DEFAULT_LOCAL_PORT,
        help="porta UDP locale fissa di Idefix per gli ACK",
    )
    parser.add_argument("--system-id", type=int, default=42, help="system ID di Idefix")
    parser.add_argument(
        "--component-id", type=int, default=191, help="component ID di Idefix"
    )
    parser.add_argument("--target-system", type=int, default=1)
    parser.add_argument("--target-component", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--retries", type=int, default=2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Tool MAVLink di Idefix per comandare o simulare i LED di ObeliCS"
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    receiver = subparsers.add_parser(
        "listen", help="simula ObeliCS: riceve e conferma solo i comandi LED"
    )
    receiver.add_argument("--bind", default="0.0.0.0")
    receiver.add_argument("--port", type=int, default=DEFAULT_PORT)
    receiver.add_argument("--system-id", type=int, default=1)
    receiver.add_argument("--component-id", type=int, default=1)
    receiver.add_argument(
        "--count", type=int, default=0, help="esce dopo N comandi accettati; 0 = sempre"
    )
    receiver.set_defaults(function=listen)

    sender = subparsers.add_parser("send", help="invia un singolo comando LED da Idefix")
    add_sender_options(sender)
    sender.add_argument("actuator", choices=sorted(MODE_TYPES))
    sender.add_argument("mode", help="modalita LED: off, bounce, spin, blink (none = off)")
    sender.set_defaults(function=send)

    test_all = subparsers.add_parser("test-all", help="prova in sequenza le quattro modalita LED")
    add_sender_options(test_all)
    test_all.set_defaults(function=send)

    shell = subparsers.add_parser(
        "interactive", help="apre una console interattiva per i LED su Idefix"
    )
    add_sender_options(shell)
    shell.set_defaults(function=interactive)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if getattr(args, "retries", 0) < 0:
        raise SystemExit("--retries non puo essere negativo")
    if getattr(args, "timeout", 1.0) <= 0:
        raise SystemExit("--timeout deve essere positivo")
    if getattr(args, "count", 0) < 0:
        raise SystemExit("--count non puo essere negativo")
    for option in ("port", "local_port"):
        value = getattr(args, option, DEFAULT_PORT)
        if not 1 <= value <= 65_535:
            raise SystemExit(f"--{option.replace('_', '-')} deve essere tra 1 e 65535")
    return args.function(args)


if __name__ == "__main__":
    raise SystemExit(main())
