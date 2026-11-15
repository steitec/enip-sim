"""The Connection Manager: Forward_Open and Forward_Close for Class 3 (connected
explicit) messaging.

A scanner opens a connection with Forward_Open, then sends CIP requests over it with
SendUnitData. The target (this simulator) assigns the O->T connection id it receives on
and echoes the originator's T->O connection id for its replies. Cyclic Class 1 implicit
I/O over UDP is a later addition; this module handles the connection bookkeeping only.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from itertools import count

from . import cip


@dataclass
class Connection:
    ot_cid: int          # O->T: the id the originator puts in packets it sends to us
    to_cid: int          # T->O: the id we put in packets we send to the originator
    serial: int          # connection serial number
    vendor: int          # originator vendor id
    orig_serial: int     # originator serial number
    ot_rpi: int          # O->T requested packet interval (us)
    to_rpi: int          # T->O requested packet interval (us)


class ConnectionManager:
    def __init__(self) -> None:
        self.connections: dict[int, Connection] = {}
        self._cids = count(0x20000001)

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
        ot_cid = next(self._cids)
        connection = Connection(ot_cid=ot_cid, to_cid=fo.to_cid, serial=fo.serial,
                                vendor=fo.vendor, orig_serial=fo.orig_serial,
                                ot_rpi=fo.ot_rpi, to_rpi=fo.to_rpi)
        self.connections[ot_cid] = connection
        reply = (struct.pack("<II", ot_cid, fo.to_cid)
                 + struct.pack("<HHI", fo.serial, fo.vendor, fo.orig_serial)
                 + struct.pack("<II", fo.ot_rpi, fo.to_rpi)
                 + bytes([0, 0]))            # application reply size 0, reserved
        return cip.SUCCESS, reply, []

    def forward_close(self, data: bytes) -> tuple[int, bytes, list[int]]:
        # priority/tick, timeout ticks, connection serial, vendor id, originator serial
        if len(data) < 10:
            return cip.INVALID_PARAMETER, b"", []
        serial, vendor, orig_serial = struct.unpack_from("<HHI", data, 2)
        for cid, connection in list(self.connections.items()):
            if (connection.serial == serial and connection.vendor == vendor
                    and connection.orig_serial == orig_serial):
                del self.connections[cid]
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


def _unsuccessful(fo: ForwardOpen) -> bytes:
    # unsuccessful Forward_Open reply: connection serial, vendor, originator serial,
    # remaining path size, reserved
    return struct.pack("<HHI", fo.serial, fo.vendor, fo.orig_serial) + bytes([0, 0])
