"""The originator side of a Class 1 connection: what a scanner or PLC does.

:meth:`enip_sim.client.EnipClient.open_io` opens the connection with Forward_Open and
returns an :class:`IOConnection`, which sends the output data (O->T) at the RPI from a
background thread and keeps the latest input data (T->O). It is meant for trying the
simulator out and for tests; a production scanner belongs in a full stack.
"""
from __future__ import annotations

import os
import socket
import struct
import threading
import time
from typing import TYPE_CHECKING, Callable

from . import cip
from . import encapsulation as enc
from .connection import NET_P2P, O2T_HEADER, SEQ_COUNT
from .io import IO_PORT, decode_io_packet, encode_io_packet

if TYPE_CHECKING:
    from .client import EnipClient

ORIGINATOR_VENDOR = 0xFFFE        # vendor id the originator puts in its Forward_Open
_RECV_POLL = 0.05


def _ncp(size: int, *, large: bool) -> int:
    if large:
        return (NET_P2P << 29) | (size & 0xFFFF)
    return (NET_P2P << 13) | (size & 0x1FF)


def build_forward_open(*, to_cid: int, serial: int, orig_serial: int, rpi_us: int,
                       timeout_multiplier: int, ot_size: int, to_size: int, path: bytes,
                       large: bool = False, vendor: int = ORIGINATOR_VENDOR) -> bytes:
    """Request data of a Class 1 cyclic (Large_)Forward_Open, point-to-point both ways."""
    ncp = "<I" if large else "<H"
    if len(path) % 2:
        path += b"\x00"
    return (bytes([0x0A, 0x0E])                                   # tick, timeout ticks
            + struct.pack("<II", 0, to_cid)
            + struct.pack("<HHI", serial, vendor, orig_serial)
            + bytes([timeout_multiplier & 0x07, 0, 0, 0])
            + struct.pack("<I", rpi_us) + struct.pack(ncp, _ncp(ot_size, large=large))
            + struct.pack("<I", rpi_us) + struct.pack(ncp, _ncp(to_size, large=large))
            + bytes([0x01, len(path) // 2]) + path)


def io_connection_path(*, config: int, consumed: int, produced: int) -> bytes:
    def logical(kind_8: int, value: int) -> bytes:
        if value <= 0xFF:
            return bytes([kind_8, value])
        return bytes([kind_8 + 1, 0]) + struct.pack("<H", value)
    return (bytes([0x20, cip.ASSEMBLY]) + logical(0x24, config)
            + logical(0x2C, consumed) + logical(0x2C, produced))


class IOConnection:
    """An open Class 1 connection, seen from the originator."""

    def __init__(self, client: "EnipClient", sock: socket.socket, *, target: tuple[str, int],
                 ot_cid: int, to_cid: int, serial: int, orig_serial: int, rpi_ms: float,
                 output_size: int, input_only: bool, path: bytes):
        self.client = client
        self.target = target
        self.ot_cid = ot_cid
        self.to_cid = to_cid
        self.rpi_ms = rpi_ms
        self.input_only = input_only
        self.packets_received = 0
        self.owns_client = False      # close the client too (Bench.open_io)
        self.last_input_time: float | None = None
        self._serial = serial
        self._orig_serial = orig_serial
        self._path = path
        self._sock = sock
        self._outputs = bytes(output_size)
        self._run = False
        self._inputs: bytes | None = None
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._threads = [threading.Thread(target=self._send_loop, daemon=True,
                                          name="enip-sim-originator-tx"),
                         threading.Thread(target=self._receive_loop, daemon=True,
                                          name="enip-sim-originator-rx")]
        for thread in self._threads:
            thread.start()

    # --- outputs -------------------------------------------------------------------
    def set_outputs(self, data: bytes) -> None:
        if len(data) != len(self._outputs):
            raise ValueError(f"outputs are {len(self._outputs)} bytes, got {len(data)}")
        with self._lock:
            self._outputs = bytes(data)

    def run(self) -> None:
        """Send with the run bit set: the device applies the outputs."""
        self._run = True

    def idle(self) -> None:
        """Send with the run bit clear: the device holds its outputs."""
        self._run = False

    # --- inputs --------------------------------------------------------------------
    @property
    def inputs(self) -> bytes | None:
        with self._lock:
            return self._inputs

    def wait_for_inputs(self, predicate: Callable[[bytes | None], bool],
                        timeout: float = 2.0) -> bytes:
        """Wait until the latest input data satisfies ``predicate``; return it."""
        deadline = time.monotonic() + timeout
        with self._changed:
            while self._inputs is None or not predicate(self._inputs):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"inputs did not match within {timeout} s "
                                       f"(last: {self._inputs!r})")
                self._changed.wait(remaining)
            return self._inputs

    # --- lifecycle -----------------------------------------------------------------
    def close(self) -> None:
        """Stop the exchange and close the connection with Forward_Close."""
        if self._stop.is_set():
            return
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=2)
        self._sock.close()
        data = (bytes([0x0A, 0x0E])
                + struct.pack("<HHI", self._serial, ORIGINATOR_VENDOR, self._orig_serial)
                + bytes([len(self._path) // 2, 0]) + self._path)
        try:
            self.client.message(cip.FORWARD_CLOSE,
                                cip.encode_path(cip.CONNECTION_MANAGER, 1), data)
        except (OSError, ConnectionError, enc.EncapsulationError, cip.CIPError):
            pass                      # the session is gone; the target times out instead
        if self.owns_client:
            self.client.close()

    def __enter__(self) -> "IOConnection":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- threads -------------------------------------------------------------------
    def _send_loop(self) -> None:
        sequence = 0
        interval = self.rpi_ms / 1000.0
        due = time.monotonic()
        while not self._stop.is_set():
            sequence += 1
            with self._lock:
                data = b"" if self.input_only else self._outputs
            packet = encode_io_packet(self.ot_cid, sequence, sequence, data, run=self._run)
            try:
                self._sock.sendto(packet, self.target)
            except OSError:
                pass
            due += interval
            if due < time.monotonic():
                due = time.monotonic() + interval
            self._stop.wait(max(0.0, due - time.monotonic()))

    def _receive_loop(self) -> None:
        while not self._stop.is_set():
            try:
                data, _ = self._sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                packet = decode_io_packet(data, with_header=False)
            except enc.EncapsulationError:
                continue
            if packet.cid != self.to_cid:
                continue
            with self._changed:
                self._inputs = packet.data
                self.packets_received += 1
                self.last_input_time = time.monotonic()
                self._changed.notify_all()


def open_io(client: "EnipClient", *, output_instance: int, input_instance: int,
            config_instance: int, output_size: int, input_size: int, rpi_ms: float = 50,
            timeout_multiplier: int = 2, input_only: bool = False,
            heartbeat_instance: int = 198, large: bool = False) -> IOConnection:
    """Open a Class 1 connection over ``client``'s session (see EnipClient.open_io)."""
    from .client import ForwardOpenError
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", 0))
    sock.settimeout(_RECV_POLL)
    my_port = sock.getsockname()[1]
    to_cid = int.from_bytes(os.urandom(4), "little") | 1
    serial = int.from_bytes(os.urandom(2), "little")
    orig_serial = int.from_bytes(os.urandom(4), "little")
    consumed = heartbeat_instance if input_only else output_instance
    path = io_connection_path(config=config_instance, consumed=consumed,
                              produced=input_instance)
    ot_size = SEQ_COUNT + O2T_HEADER + (0 if input_only else output_size)
    data = build_forward_open(to_cid=to_cid, serial=serial, orig_serial=orig_serial,
                              rpi_us=int(rpi_ms * 1000), timeout_multiplier=timeout_multiplier,
                              ot_size=ot_size, to_size=SEQ_COUNT + input_size, path=path,
                              large=large)
    service = cip.LARGE_FORWARD_OPEN if large else cip.FORWARD_OPEN
    items = [(enc.ITEM_SOCKADDR_T2O, enc.encode_sockaddr("0.0.0.0", my_port))]
    try:
        status, additional, reply, reply_items = client.request_items(
            service, cip.encode_path(cip.CONNECTION_MANAGER, 1), data, items)
    except BaseException:
        sock.close()
        raise
    if status != cip.SUCCESS:
        sock.close()
        raise ForwardOpenError(status, additional)
    ot_cid, echoed = struct.unpack_from("<II", reply, 0)
    port = IO_PORT
    for type_id, item in reply_items:
        if type_id == enc.ITEM_SOCKADDR_O2T:
            port = enc.decode_sockaddr(item)[1] or IO_PORT
    return IOConnection(client, sock, target=(client.host, port), ot_cid=ot_cid,
                        to_cid=echoed, serial=serial, orig_serial=orig_serial, rpi_ms=rpi_ms,
                        output_size=output_size, input_only=input_only, path=path)
