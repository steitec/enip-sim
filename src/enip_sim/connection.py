"""The Connection Manager: Forward_Open and Forward_Close for Class 3 (connected
explicit) messaging and Class 1 (implicit) I/O.

A scanner opens a connection with Forward_Open. For Class 3 it then sends CIP requests
over it with SendUnitData; for Class 1 the assembly data runs over UDP (see :mod:`.io`).
The target (this simulator) assigns the O->T connection id it receives on and echoes
the originator's T->O connection id. This module parses and validates the Forward_Open
and keeps the connection bookkeeping.
"""
from __future__ import annotations

import struct
import threading
from dataclasses import dataclass
from itertools import count
from typing import TYPE_CHECKING, Callable

from . import cip

if TYPE_CHECKING:
    from .device import IOPoints

# extended status codes of a failed Forward_Open (general status 0x01)
EXT_DUPLICATE = 0x0100           # connection in use / duplicate Forward_Open
EXT_TRANSPORT = 0x0103           # transport class and trigger not supported
EXT_OWNERSHIP = 0x0106           # ownership conflict
EXT_CONNECTION_TYPE = 0x0108     # invalid network connection parameter (type)
EXT_SIZE = 0x0109                # invalid connection size; additional = expected size
EXT_RPI = 0x0111                 # RPI not supported
EXT_APP_PATH = 0x0117            # invalid produced or consumed application path
EXT_CONFIG_PATH = 0x0118         # invalid configuration application path

MIN_RPI = 10000                  # us; the simulator does not go faster than 10 ms
O2T_HEADER = 4                   # 32-bit run/idle header on O->T data
SEQ_COUNT = 2                    # 16-bit CIP sequence count on Class 1 data


@dataclass
class Connection:
    ot_cid: int          # O->T: the id the originator puts in packets it sends to us
    to_cid: int          # T->O: the id we put in packets we send to the originator
    serial: int          # connection serial number
    vendor: int          # originator vendor id
    orig_serial: int     # originator serial number
    ot_rpi: int          # O->T requested packet interval (us)
    to_rpi: int          # T->O requested packet interval (us)
    kind: str = "class3"             # "class3", "exclusive" or "input_only"
    timeout_multiplier: int = 0      # code 0-7: timeout = (4 << code) * O->T RPI
    ot_size: int = 0                 # NCP sizes (bytes, incl. sequence count/header)
    to_size: int = 0
    peer: tuple[str, int] | None = None    # where T->O packets go (set by the server)
    run: bool = False                # last run/idle bit received
    last_rx: float = 0.0             # monotonic time of the last O->T packet

    @property
    def triad(self) -> tuple[int, int, int]:
        return self.serial, self.vendor, self.orig_serial

    @property
    def is_io(self) -> bool:
        return self.kind != "class3"


class ConnectionManager:
    def __init__(self, device_io: Callable[[], "IOPoints | None"] | None = None) -> None:
        self.connections: dict[int, Connection] = {}
        self._cids = count(0x20000001)
        self._device_io = device_io or (lambda: None)
        # sessions run in their own threads: checking for a duplicate or an owner and
        # adding the connection must be one step, or two owners can both get in
        self._lock = threading.RLock()
        #: called with each new connection, and with (connection, reason) when one ends
        self.on_open: Callable[[Connection], None] | None = None
        self.on_close: Callable[[Connection, str], None] | None = None

    def lookup(self, cid: int) -> Connection | None:
        return self.connections.get(cid)

    def forward_open(self, data: bytes, *, large: bool = False,
                     reject_status: int | None = None) -> tuple[int, bytes, list[int]]:
        try:
            fo = parse_forward_open(data, large=large)
        except struct.error:
            return cip.INVALID_PARAMETER, b"", []
        if reject_status is not None:
            return cip.CONNECTION_FAILURE, _unsuccessful(fo), [reject_status]
        transport_class = fo.transport & TRANSPORT_CLASS_MASK
        if transport_class not in (1, 3):
            return cip.CONNECTION_FAILURE, _unsuccessful(fo), [EXT_TRANSPORT]
        with self._lock:
            kind = "class3"
            if transport_class == 1:
                kind, failure = self._check_class1(fo)
                if failure is not None:
                    return cip.CONNECTION_FAILURE, _unsuccessful(fo), failure
            ot_cid = next(self._cids)
            connection = Connection(ot_cid=ot_cid, to_cid=fo.to_cid, serial=fo.serial,
                                    vendor=fo.vendor, orig_serial=fo.orig_serial,
                                    ot_rpi=fo.ot_rpi, to_rpi=fo.to_rpi, kind=kind,
                                    timeout_multiplier=fo.timeout_multiplier,
                                    ot_size=fo.ot_ncp.size, to_size=fo.to_ncp.size)
            self.connections[ot_cid] = connection
        if self.on_open is not None:
            self.on_open(connection)
        reply = (struct.pack("<II", ot_cid, fo.to_cid)
                 + struct.pack("<HHI", fo.serial, fo.vendor, fo.orig_serial)
                 + struct.pack("<II", fo.ot_rpi, fo.to_rpi)
                 + bytes([0, 0]))            # application reply size 0, reserved
        return cip.SUCCESS, reply, []

    def _check_class1(self, fo: ForwardOpen) -> tuple[str, list[int] | None]:
        return _class1_failure(fo, self._device_io(), list(self.connections.values()))

    def close(self, connection: Connection, reason: str) -> None:
        """End a connection (Forward_Close, or a Class 1 timeout)."""
        with self._lock:
            if self.connections.pop(connection.ot_cid, None) is None:
                return
        if self.on_close is not None:
            self.on_close(connection, reason)

    def forward_close(self, data: bytes) -> tuple[int, bytes, list[int]]:
        # priority/tick, timeout ticks, connection serial, vendor id, originator serial
        if len(data) < 10:
            return cip.INVALID_PARAMETER, b"", []
        serial, vendor, orig_serial = struct.unpack_from("<HHI", data, 2)
        with self._lock:
            matching = list(self.connections.values())
        for connection in matching:
            if (connection.serial == serial and connection.vendor == vendor
                    and connection.orig_serial == orig_serial):
                self.close(connection, "close")
        reply = struct.pack("<HHI", serial, vendor, orig_serial) + bytes([0, 0])
        return cip.SUCCESS, reply, []


NET_NULL = 0
NET_MULTICAST = 1
NET_P2P = 2
TRANSPORT_CLASS_MASK = 0x0F
TRIGGER_MASK = 0x70


@dataclass
class NetParams:
    """Network connection parameters of one direction."""
    conn_type: int       # 0 null, 1 multicast, 2 point-to-point
    size: int            # connection size in bytes (includes sequence count / header)
    fixed: bool


@dataclass
class ForwardOpen:
    ot_cid: int
    to_cid: int
    serial: int
    vendor: int
    orig_serial: int
    timeout_multiplier: int
    ot_rpi: int
    ot_ncp: NetParams
    to_rpi: int
    to_ncp: NetParams
    transport: int
    path: bytes


def _ncp16(value: int) -> NetParams:
    return NetParams((value >> 13) & 3, value & 0x1FF, not (value >> 9) & 1)


def _ncp32(value: int) -> NetParams:
    return NetParams((value >> 29) & 3, value & 0xFFFF, not (value >> 25) & 1)


def parse_forward_open(data: bytes, *, large: bool) -> ForwardOpen:
    """Decode Forward_Open (or, with ``large``, Large_Forward_Open) request data.

    Raises struct.error when the data is too short."""
    ot_cid, to_cid = struct.unpack_from("<II", data, 2)
    serial, vendor, orig_serial = struct.unpack_from("<HHI", data, 10)
    (multiplier,) = struct.unpack_from("<B", data, 18)
    if large:
        ot_rpi, ot_raw, to_rpi, to_raw = struct.unpack_from("<IIII", data, 22)
        ot_ncp, to_ncp = _ncp32(ot_raw), _ncp32(to_raw)
        offset = 38
    else:
        ot_rpi, ot_raw, to_rpi, to_raw = struct.unpack_from("<IHIH", data, 22)
        ot_ncp, to_ncp = _ncp16(ot_raw), _ncp16(to_raw)
        offset = 34
    transport, words = struct.unpack_from("<BB", data, offset)
    path = data[offset + 2:offset + 2 + 2 * words]
    return ForwardOpen(ot_cid=ot_cid, to_cid=to_cid, serial=serial, vendor=vendor,
                       orig_serial=orig_serial, timeout_multiplier=multiplier & 0x07,
                       ot_rpi=ot_rpi, ot_ncp=ot_ncp, to_rpi=to_rpi, to_ncp=to_ncp,
                       transport=transport, path=path)


def parse_connection_path(path: bytes) -> tuple[int | None, list[int]]:
    """Return (configuration instance, connection points) of a connection path.

    Port segments, the electronic key and data segments are skipped; the key is not
    checked. Connection points come in path order (consumed O->T first, then produced
    T->O)."""
    config: int | None = None
    points: list[int] = []
    i = 0
    while i < len(path):
        seg = path[i]
        if seg == 0x00:                                   # pad byte
            i += 1
        elif seg & 0xE0 == 0x00:                          # port segment
            if seg & 0x10:                                # extended link address
                size = path[i + 1]
                i += 2 + size + (size & 1)
            else:
                i += 2
            if seg & 0x0F == 0x0F:                        # extended port number
                i += 2
        elif seg == 0x34:                                 # electronic key
            i += 10                                       # 0x34, format 4, 8 bytes
        elif seg in (0x20, 0x28, 0x2C, 0x24, 0x30):       # 8-bit logical
            if seg == 0x24:
                config = path[i + 1]
            elif seg == 0x2C:
                points.append(path[i + 1])
            i += 2
        elif seg in (0x21, 0x25, 0x29, 0x2D, 0x31):       # 16-bit logical
            value = struct.unpack_from("<H", path, i + 2)[0]
            if seg == 0x25:
                config = value
            elif seg == 0x2D:
                points.append(value)
            i += 4
        elif seg in (0x22, 0x26, 0x2A, 0x2E, 0x32):       # 32-bit logical
            value = struct.unpack_from("<I", path, i + 2)[0]
            if seg == 0x26:
                config = value
            elif seg == 0x2E:
                points.append(value)
            i += 6
        elif seg == 0x80:                                 # simple data segment
            i += 2 + path[i + 1] * 2
        else:
            break
    return config, points


def _class1_failure(fo: ForwardOpen, io: "IOPoints | None",
                    open_connections: list[Connection]) -> tuple[str, list[int] | None]:
    """Validate a Class 1 Forward_Open; return (kind, None) or ("", additional status)."""
    if fo.transport & TRIGGER_MASK:                       # only cyclic
        return "", [EXT_TRANSPORT]
    if io is None:
        return "", [EXT_APP_PATH]
    config, points = parse_connection_path(fo.path)
    if len(points) != 2 or points[1] != io.input_instance:
        return "", [EXT_APP_PATH]
    if points[0] == io.output_instance:
        kind = "exclusive"
    elif points[0] == io.heartbeat_instance:
        kind = "input_only"
    else:
        return "", [EXT_APP_PATH]
    if config != io.config_instance:
        return "", [EXT_CONFIG_PATH]
    if fo.ot_ncp.conn_type != NET_P2P or fo.to_ncp.conn_type != NET_P2P:
        return "", [EXT_CONNECTION_TYPE]
    if fo.ot_rpi < MIN_RPI or fo.to_rpi < MIN_RPI:
        return "", [EXT_RPI]
    full_ot = SEQ_COUNT + O2T_HEADER
    if kind == "exclusive" and fo.ot_ncp.size != io.output_size + full_ot:
        return "", [EXT_SIZE, io.output_size + full_ot]
    if kind == "input_only" and fo.ot_ncp.size not in (SEQ_COUNT, full_ot):
        return "", [EXT_SIZE, full_ot]
    if fo.to_ncp.size != io.input_size + SEQ_COUNT:
        return "", [EXT_SIZE, io.input_size + SEQ_COUNT]
    triad = (fo.serial, fo.vendor, fo.orig_serial)
    if any(c.triad == triad for c in open_connections):
        return "", [EXT_DUPLICATE]
    if kind == "exclusive" and any(c.kind == "exclusive" for c in open_connections):
        return "", [EXT_OWNERSHIP]
    return kind, None


def _unsuccessful(fo: ForwardOpen) -> bytes:
    # unsuccessful Forward_Open reply: connection serial, vendor, originator serial,
    # remaining path size, reserved
    return struct.pack("<HHI", fo.serial, fo.vendor, fo.orig_serial) + bytes([0, 0])
