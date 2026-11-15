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


# --- full Forward_Open parsing (0.2.0) ---------------------------------------------
from enip_sim.connection import NET_P2P, parse_connection_path, parse_forward_open  # noqa: E402
from enip_helpers import forward_open_data, io_path, ncp16, ncp32  # noqa: E402


def test_parse_forward_open_normal():
    data = forward_open_data(path=io_path(config=102, consumed=101, produced=100),
                             multiplier=3, ot_rpi=20000, to_rpi=40000,
                             ot_ncp=ncp16(size=8), to_ncp=ncp16(size=4, fixed=False))
    fo = parse_forward_open(data, large=False)
    assert (fo.timeout_multiplier, fo.ot_rpi, fo.to_rpi) == (3, 20000, 40000)
    assert (fo.ot_ncp.conn_type, fo.ot_ncp.size, fo.ot_ncp.fixed) == (NET_P2P, 8, True)
    assert (fo.to_ncp.size, fo.to_ncp.fixed) == (4, False)
    assert fo.transport == 0x01
    assert fo.path == io_path(config=102, consumed=101, produced=100)
    assert (fo.serial, fo.vendor, fo.orig_serial, fo.to_cid) == (0x1234, 0x004B, 0xDEADBEEF, 0x11111111)


def test_parse_large_forward_open_reads_rpis_at_large_offsets():
    data = forward_open_data(path=io_path(config=1, consumed=20, produced=70), large=True,
                             ot_rpi=10000, to_rpi=50000,
                             ot_ncp=ncp32(size=600), to_ncp=ncp32(size=510, conn_type=1))
    fo = parse_forward_open(data, large=True)
    assert (fo.ot_rpi, fo.to_rpi) == (10000, 50000)
    assert fo.ot_ncp.size == 600 and fo.to_ncp.size == 510
    assert fo.to_ncp.conn_type == 1
    assert fo.path == io_path(config=1, consumed=20, produced=70)


def test_parse_connection_path_skips_key_and_port():
    key = bytes([0x34, 0x04]) + struct.pack("<HHHBB", 1, 7, 16, 1, 0)
    port = bytes([0x01, 0x00])          # port 1, link 0
    path = port + key + io_path(config=1, consumed=101, produced=100)
    assert parse_connection_path(path) == (1, [101, 100])


def test_parse_connection_path_16bit_point():
    path = bytes([0x20, 0x04, 0x24, 0x01, 0x2D, 0x00, 0x65, 0x00, 0x2D, 0x00, 0x64, 0x00])
    assert parse_connection_path(path) == (1, [101, 100])


def test_parse_connection_path_skips_data_segment():
    data_segment = bytes([0x80, 0x01, 0xAA, 0xBB])
    path = io_path(config=1, consumed=101, produced=100) + data_segment
    assert parse_connection_path(path) == (1, [101, 100])


def test_class3_connection_reads_correct_rpis_for_large_forward_open():
    mgr = ConnectionManager()
    data = forward_open_data(path=bytes([0x20, 0x02, 0x24, 0x01]), transport=0xA3,
                             large=True, ot_rpi=111000, to_rpi=222000)
    status, reply, _ = mgr.forward_open(data, large=True)[:3]
    assert status == cip.SUCCESS
    assert struct.unpack_from("<II", reply, 16) == (111000, 222000)
