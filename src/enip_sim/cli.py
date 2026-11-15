"""Command line for enip-sim.

    enip-sim profiles                       # the bundled device profiles
    enip-sim info drive                     # a profile's identity and assemblies
    enip-sim serve drive                    # one device on EtherNet/IP 127.0.0.1:44818
    enip-sim serve --bench bench.yaml --host 0.0.0.0
    enip-sim identity --host 192.168.1.10   # List Identity of a running device
    enip-sim read 0x01 1 1                   # Get_Attribute_Single (class, instance, attr)
    enip-sim write 0x04 20 3 0100f401        # Set_Attribute_Single with hex data
    enip-sim assembly 70                     # read an assembly and decode a drive status
    enip-sim io --out 0100f401 --seconds 5   # Class 1 I/O: run the drive at 50.0 Hz
    enip-sim inject trip code=5              # fault injection over the control channel
    enip-sim clear                           # clear all faults
    enip-sim get                             # read the model's variables
    enip-sim list                            # devices, faults and request counts
"""
from __future__ import annotations

import argparse
import errno
import json
import os
import signal
import struct
import sys
import threading
import time
from typing import Any

from . import __version__, cip
from .bench import Bench, BenchError
from .client import EnipClient, EnipError, ForwardOpenError
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
                    control_port=control_port, io_port=args.io_port)
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


# device type (Identity attribute 2) -> bundled profile, for `io` without --profile
_PROFILE_BY_TYPE = {0x02: "drive", 0x07: "io-block"}


def _cmd_io(args) -> int:
    try:
        client = _client(args).connect()
    except (OSError, ConnectionError) as error:
        print(f"{PROG}: {error}", file=sys.stderr)
        return 1
    try:
        profile = args.profile
        if profile is None:
            device_type = client.list_identity().get("device_type")
            profile = _PROFILE_BY_TYPE.get(device_type)
            if profile is None:
                print(f"{PROG}: unknown device type {device_type!r}; name a --profile",
                      file=sys.stderr)
                return 2
        try:
            io = build_device(profile).io
        except ProfileError as error:
            print(f"{PROG}: {error}", file=sys.stderr)
            return 2
        if io is None:
            print(f"{PROG}: profile {profile!r} has no I/O assemblies", file=sys.stderr)
            return 2
        outputs = bytes.fromhex(args.out) if args.out else bytes(io.output_size)
        if len(outputs) != io.output_size and not args.input_only:
            print(f"{PROG}: --out must be {io.output_size} bytes for {profile}", file=sys.stderr)
            return 2
        connection = client.open_io(
            output_instance=io.output_instance, input_instance=io.input_instance,
            config_instance=io.config_instance, output_size=io.output_size,
            input_size=io.input_size, heartbeat_instance=io.heartbeat_instance,
            rpi_ms=args.rpi, input_only=args.input_only)
    except ForwardOpenError as error:
        print(f"{PROG}: Forward_Open rejected: {error}", file=sys.stderr)
        client.close()
        return 1
    except (OSError, ConnectionError, EnipError) as error:
        print(f"{PROG}: {error}", file=sys.stderr)
        client.close()
        return 1
    with connection:
        if not args.input_only:
            connection.set_outputs(outputs)
            connection.idle() if args.idle else connection.run()
        mode = "input-only" if args.input_only else ("idle" if args.idle else "run")
        print(f"{PROG}: {profile} I/O open ({mode}, RPI {args.rpi:g} ms), "
              f"O->T 0x{connection.ot_cid:08x}, T->O 0x{connection.to_cid:08x}", flush=True)
        start = time.monotonic()
        last = None
        reader_gone = False
        try:
            while time.monotonic() - start < args.seconds:
                data = connection.inputs
                if data is not None and data != last:
                    line = f"{time.monotonic() - start:7.3f} s  in {data.hex()}"
                    status = _drive_status(data) if profile == "drive" else ""
                    print(line + (status if status else ""), flush=True)
                    last = data
                time.sleep(min(args.rpi / 1000, 0.05))
        except OSError as error:          # `enip-sim io ... | head`: the reader is gone
            # BrokenPipeError; Windows sometimes reports a closed pipe as EINVAL
            if not isinstance(error, BrokenPipeError) and error.errno != errno.EINVAL:
                raise
            _silence_stdout()
            reader_gone = True
        received = connection.packets_received
    client.close()                        # after the with block: Forward_Close went out
    if reader_gone:
        return 0
    print(f"{PROG}: {received} input packets in {args.seconds:g} s", flush=True)
    return 0


def _silence_stdout() -> None:
    """Point stdout at devnull so the flush at exit does not raise BrokenPipeError again."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        os.close(devnull)
    except (OSError, ValueError, AttributeError):
        pass                              # not a real file (tests); nothing to flush


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
    p.add_argument("--io-port", type=int, default=None,
                   help="UDP port for Class 1 I/O (default 2222; 0 = any free port)")
    p.set_defaults(func=_cmd_serve)

    p = sub.add_parser("io", help="open a Class 1 I/O connection and print the inputs")
    add_wire(p)
    p.add_argument("--profile", help="bundled profile of the device (default: from its "
                                     "device type)")
    p.add_argument("--out", help="output data as hex (default all zero)")
    p.add_argument("--rpi", type=float, default=50.0, help="RPI in ms (default 50)")
    p.add_argument("--seconds", type=float, default=5.0, help="how long to run (default 5)")
    p.add_argument("--idle", action="store_true", help="send with the run bit clear")
    p.add_argument("--input-only", action="store_true",
                   help="input-only connection (heartbeat, no outputs)")
    p.set_defaults(func=_cmd_io)

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
