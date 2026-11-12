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

    def forward_open(self, data: bytes, *,
                     reject_status: int | None = None) -> tuple[int, bytes, list[int]]:
        try:
            fields = _parse_forward_open(data)
        except struct.error:
            return cip.INVALID_PARAMETER, b"", []
        if reject_status is not None:
            return cip.CONNECTION_FAILURE, _unsuccessful(fields), [reject_status]
        ot_cid = next(self._cids)
        connection = Connection(ot_cid=ot_cid, to_cid=fields["to_cid"],
                                serial=fields["serial"], vendor=fields["vendor"],
                                orig_serial=fields["orig_serial"],
                                ot_rpi=fields["ot_rpi"], to_rpi=fields["to_rpi"])
        self.connections[ot_cid] = connection
        reply = (struct.pack("<II", ot_cid, fields["to_cid"])
                 + struct.pack("<HHI", fields["serial"], fields["vendor"],
                               fields["orig_serial"])
                 + struct.pack("<II", fields["ot_rpi"], fields["to_rpi"])
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


def _parse_forward_open(data: bytes) -> dict:
    ot_cid, to_cid = struct.unpack_from("<II", data, 2)
    serial, vendor = struct.unpack_from("<HH", data, 10)
    (orig_serial,) = struct.unpack_from("<I", data, 14)
    (ot_rpi,) = struct.unpack_from("<I", data, 22)
    (to_rpi,) = struct.unpack_from("<I", data, 28)
    return {"ot_cid": ot_cid, "to_cid": to_cid, "serial": serial, "vendor": vendor,
            "orig_serial": orig_serial, "ot_rpi": ot_rpi, "to_rpi": to_rpi}


def _unsuccessful(fields: dict) -> bytes:
    # unsuccessful Forward_Open reply: connection serial, vendor, originator serial,
    # remaining path size, reserved
    return struct.pack("<HHI", fields["serial"], fields["vendor"],
                       fields["orig_serial"]) + bytes([0, 0])
