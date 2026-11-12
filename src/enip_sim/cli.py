"""Command line for enip-sim.

    enip-sim profiles                       # the bundled device profiles
    enip-sim info drive                     # a profile's identity and assemblies
    enip-sim serve drive                    # one device on EtherNet/IP 127.0.0.1:44818
    enip-sim serve --bench bench.yaml --host 0.0.0.0
    enip-sim identity --host 192.168.1.10   # List Identity of a running device
    enip-sim read 0x01 1 1                   # Get_Attribute_Single (class, instance, attr)
    enip-sim write 0x04 20 3 0100f401        # Set_Attribute_Single with hex data
    enip-sim assembly 70                     # read an assembly and decode a drive status
    enip-sim inject trip code=5              # fault injection over the control channel
    enip-sim clear                           # clear all faults
    enip-sim get                             # read the model's variables
    enip-sim list                            # devices, faults and request counts
"""
from __future__ import annotations

import argparse
import json
import signal
import struct
import sys
import threading
from typing import Any

from . import __version__, cip
from .bench import Bench, BenchError
from .client import EnipClient, EnipError
from .control import CONTROL_PORT, send_control
from .profile import ProfileError, bundled_profiles, build_device
from .server import TCP_PORT

PROG = "enip-sim"


# --- helpers ------------------------------------------------------------------------

def _int(text: str) -> int:
    return int(text, 0)


def _coerce(value: str) -> Any:
    low = value.lower()
    if low in ("true", "false"):
        return low == "true"
    for convert in (lambda v: int(v, 0), float):
        try:
            return convert(value)
        except ValueError:
            continue
    return value


def _params(pairs: list[str]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"{PROG}: parameter {pair!r} must be name=value")
        name, value = pair.split("=", 1)
        params[name] = _coerce(value)
    return params


def _client(args) -> EnipClient:
    return EnipClient(args.host, args.port, timeout=args.timeout)


def _drive_status(data: bytes) -> str:
    if len(data) < 4:
        return ""
    status, speed = struct.unpack_from("<HH", data, 0)
    bits = [name for bit, name in [(0x1, "running"), (0x2, "ready"), (0x4, "faulted"),
                                   (0x8, "at reference"), (0x10, "reverse")] if status & bit]
    return f"  status 0x{status:04x} ({', '.join(bits) or 'idle'}), speed {speed / 10:.1f} Hz"


# --- commands -----------------------------------------------------------------------

def _cmd_profiles(args) -> int:
    for name in bundled_profiles():
        device = build_device(name)
        print(f"{name:18}{_identity_name(device)}")
    return 0


def _identity_name(device) -> str:
    identity = device.find(cip.IDENTITY, 1)
    if identity is None:
        return ""
    return identity.attributes[7].get_bytes()[1:].decode("latin-1")


def _cmd_info(args) -> int:
    try:
        device = build_device(args.profile)
    except ProfileError as error:
        print(f"{PROG}: {error}", file=sys.stderr)
        return 2
    identity = device.find(cip.IDENTITY, 1)
    attrs = identity.attributes
    vendor, dtype, code = (struct.unpack("<H", attrs[n].get_bytes())[0] for n in (1, 2, 3))
    major, minor = attrs[4].get_bytes()
    print(f"{device.name}: {_identity_name(device)}")
    print(f"  vendor 0x{vendor:04x}  device type 0x{dtype:02x}  product code 0x{code:04x}"
          f"  revision {major}.{minor}")
    assemblies = sorted(i for (c, i) in device.objects if c == cip.ASSEMBLY)
    if assemblies:
        print(f"  assemblies: {', '.join(str(a) for a in assemblies)}")
    if device.model is not None:
        print(f"  model: {type(device.model).__name__}")
    return 0


def _cmd_serve(args) -> int:
    try:
        bench = Bench.from_yaml(args.bench) if args.bench else Bench.single(args.profile)
    except BenchError as error:
        print(f"{PROG}: {error}", file=sys.stderr)
        return 2
    control_port = None if args.no_control else args.control_port
    try:
        bench.start(host=args.host, port=args.port, control_host=args.control_host,
                    control_port=control_port)
    except BenchError as error:
        print(f"{PROG}: {error}", file=sys.stderr)
        return 2
    where = bench.describe()
    control = f", control on :{bench.control_port}" if bench.control_port else ""
    print(f"{PROG}: {where}{control}", flush=True)
    print(f"  {_identity_name(bench.device)}", flush=True)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda *_: stop.set())
        except (ValueError, OSError):  # not in the main thread / not on this platform
            pass
    try:
        while not stop.wait(0.5):
            pass
    finally:
        bench.stop()
    return 0


def _cmd_identity(args) -> int:
    try:
        with _client(args) as client:
            identity = client.list_identity()
    except (OSError, ConnectionError) as error:
        print(f"{PROG}: {error}", file=sys.stderr)
        return 1
    print(f"{identity['product_name']}")
    print(f"  vendor 0x{identity['vendor_id']:04x}  device type 0x{identity['device_type']:02x}"
          f"  product code 0x{identity['product_code']:04x}"
          f"  revision {identity['revision']['major']}.{identity['revision']['minor']}"
          f"  serial 0x{identity['serial']:08x}")
    return 0


def _cmd_read(args) -> int:
    try:
        with _client(args) as client:
            data = client.get_attribute_single(args.cls, args.instance, args.attribute)
    except EnipError as error:
        print(f"{PROG}: {error}", file=sys.stderr)
        return 1
    except (OSError, ConnectionError) as error:
        print(f"{PROG}: {error}", file=sys.stderr)
        return 1
    print(data.hex())
    return 0


def _cmd_write(args) -> int:
    try:
        payload = bytes.fromhex(args.data)
    except ValueError:
        print(f"{PROG}: data must be hex, e.g. 0100f401", file=sys.stderr)
        return 2
    try:
        with _client(args) as client:
            client.set_attribute_single(args.cls, args.instance, args.attribute, payload)
    except EnipError as error:
        print(f"{PROG}: {error}", file=sys.stderr)
        return 1
    except (OSError, ConnectionError) as error:
        print(f"{PROG}: {error}", file=sys.stderr)
        return 1
    return 0


def _cmd_assembly(args) -> int:
    try:
        with _client(args) as client:
            data = client.get_attribute_single(cip.ASSEMBLY, args.instance, 3)
    except EnipError as error:
        print(f"{PROG}: {error}", file=sys.stderr)
        return 1
    except (OSError, ConnectionError) as error:
        print(f"{PROG}: {error}", file=sys.stderr)
        return 1
    print(f"assembly {args.instance}: {data.hex()}")
    status = _drive_status(data)
    if status:
        print(status)
    return 0


def _control_call(args, request: dict) -> int:
    try:
        answer = send_control(request, host=args.control_host, port=args.control_port)
    except OSError as error:
        print(f"{PROG}: control channel: {error}", file=sys.stderr)
        return 1
    if not answer.get("ok"):
        print(f"{PROG}: {answer.get('error', 'request failed')}", file=sys.stderr)
        return 1
    return answer


def _cmd_inject(args) -> int:
    request = {"inject": args.fault, "params": _params(args.params)}
    if args.device:
        request["device"] = args.device
    answer = _control_call(args, request)
    return answer if isinstance(answer, int) else 0


def _cmd_clear(args) -> int:
    request = {"clear": args.fault if args.fault else True}
    if args.device:
        request["device"] = args.device
    answer = _control_call(args, request)
    return answer if isinstance(answer, int) else 0


def _cmd_get(args) -> int:
    request = {"get": args.names if args.names else True}
    if args.device:
        request["device"] = args.device
    answer = _control_call(args, request)
    if isinstance(answer, int):
        return answer
    print(json.dumps(answer["values"], indent=2))
    return 0


def _cmd_list(args) -> int:
    answer = _control_call(args, {"list": True})
    if isinstance(answer, int):
        return answer
    print(json.dumps(answer["devices"], indent=2))
    return 0


# --- parser -------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG, description="Simulated EtherNet/IP devices.")
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_wire(p):
        p.add_argument("--host", default="127.0.0.1")
        p.add_argument("--port", type=int, default=TCP_PORT)
        p.add_argument("--timeout", type=float, default=5.0)

    def add_control(p):
        p.add_argument("--control-host", default="127.0.0.1")
        p.add_argument("--control-port", type=int, default=CONTROL_PORT)
        p.add_argument("--device", help="device name (a bench serves one device)")

    p = sub.add_parser("profiles", help="list the bundled profiles")
    p.set_defaults(func=_cmd_profiles)

    p = sub.add_parser("info", help="show a profile's identity and assemblies")
    p.add_argument("profile")
    p.set_defaults(func=_cmd_info)

    p = sub.add_parser("serve", help="run a device (or a bench) and serve EtherNet/IP")
    p.add_argument("profile", nargs="?", default="drive")
    p.add_argument("--bench", help="a bench/profile YAML file instead of a bundled profile")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=TCP_PORT)
    p.add_argument("--control-host", default="127.0.0.1")
    p.add_argument("--control-port", type=int, default=CONTROL_PORT)
    p.add_argument("--no-control", action="store_true", help="do not open the control channel")
    p.set_defaults(func=_cmd_serve)

    p = sub.add_parser("identity", help="List Identity of a running device")
    add_wire(p)
    p.set_defaults(func=_cmd_identity)

    p = sub.add_parser("read", help="Get_Attribute_Single (prints hex)")
    p.add_argument("cls", type=_int, metavar="CLASS")
    p.add_argument("instance", type=_int)
    p.add_argument("attribute", type=_int)
    add_wire(p)
    p.set_defaults(func=_cmd_read)

    p = sub.add_parser("write", help="Set_Attribute_Single with hex data")
    p.add_argument("cls", type=_int, metavar="CLASS")
    p.add_argument("instance", type=_int)
    p.add_argument("attribute", type=_int)
    p.add_argument("data", help="hex, e.g. 0100f401")
    add_wire(p)
    p.set_defaults(func=_cmd_write)

    p = sub.add_parser("assembly", help="read an assembly's data (decodes a drive status)")
    p.add_argument("instance", type=_int)
    add_wire(p)
    p.set_defaults(func=_cmd_assembly)

    p = sub.add_parser("inject", help="inject a fault via the control channel")
    p.add_argument("fault")
    p.add_argument("params", nargs="*", metavar="name=value")
    add_control(p)
    p.set_defaults(func=_cmd_inject)

    p = sub.add_parser("clear", help="clear a fault (or all)")
    p.add_argument("fault", nargs="?")
    add_control(p)
    p.set_defaults(func=_cmd_clear)

    p = sub.add_parser("get", help="read the model's variables")
    p.add_argument("names", nargs="*")
    add_control(p)
    p.set_defaults(func=_cmd_get)

    p = sub.add_parser("list", help="devices, faults and request counts")
    add_control(p)
    p.set_defaults(func=_cmd_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
