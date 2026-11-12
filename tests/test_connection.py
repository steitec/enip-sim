"""Connection Manager: Forward_Open / Forward_Close bookkeeping."""
from __future__ import annotations

import struct

from enip_sim import cip
from enip_sim.connection import ConnectionManager


def _forward_open_data(*, to_cid=0x11111111, serial=0x1234, vendor=0x004B,
                       orig_serial=0xDEADBEEF, ot_rpi=10000, to_rpi=10000):
    return (bytes([0x06, 0x9C])                                   # tick, timeout
            + struct.pack("<II", 0x00000000, to_cid)             # O->T, T->O CID
            + struct.pack("<HHI", serial, vendor, orig_serial)
            + bytes([0x03]) + b"\x00\x00\x00"                    # mult, reserved
            + struct.pack("<IH", ot_rpi, 0x4002)                 # O->T RPI, params
            + struct.pack("<IH", to_rpi, 0x4002)                 # T->O RPI, params
            + bytes([0xA3])                                      # transport class 3
            + bytes([0x02]) + cip.encode_path(cip.MESSAGE_ROUTER, 1))


def test_forward_open_assigns_connection_and_echoes_to_cid():
    mgr = ConnectionManager()
    status, reply, _ = mgr.forward_open(_forward_open_data(to_cid=0x22223333))
    assert status == cip.SUCCESS
    ot_cid, to_cid = struct.unpack_from("<II", reply, 0)
    assert to_cid == 0x22223333                 # the originator's T->O id is echoed
    assert ot_cid in mgr.connections            # the target assigned the O->T id
    assert mgr.lookup(ot_cid).serial == 0x1234


def test_forward_open_reject_returns_connection_failure():
    mgr = ConnectionManager()
    status, _, additional = mgr.forward_open(_forward_open_data(), reject_status=0x0204)
    assert status == cip.CONNECTION_FAILURE
    assert additional == [0x0204]
    assert mgr.connections == {}


def test_forward_close_removes_the_connection():
    mgr = ConnectionManager()
    mgr.forward_open(_forward_open_data(serial=0x5A5A, vendor=1, orig_serial=7))
    close = bytes([0x06, 0x9C]) + struct.pack("<HHI", 0x5A5A, 1, 7)
    status, _, _ = mgr.forward_close(close)
    assert status == cip.SUCCESS
    assert mgr.connections == {}
