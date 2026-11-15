"""Class 1 Forward_Open validation against the device's I/O points (0.2.0)."""
from __future__ import annotations

import struct

import pytest

from enip_sim import cip
from enip_sim.connection import ConnectionManager
from enip_sim.device import IOPoints
from enip_sim.profile import build_device
from enip_helpers import forward_open_data, io_path, ncp16

IO = IOPoints(output_instance=101, input_instance=100, config_instance=102,
              heartbeat_instance=198, output_size=2, input_size=2)


def manager(io=IO):
    opened = []
    mgr = ConnectionManager(device_io=lambda: io)
    mgr.on_open = opened.append
    return mgr, opened


def owner(**kw):
    """A valid exclusive-owner request for IO, with overrides."""
    args = dict(path=io_path(config=102, consumed=101, produced=100),
                ot_ncp=ncp16(size=2 + 6), to_ncp=ncp16(size=2 + 2))
    args.update(kw)
    return forward_open_data(**args)


def rejected(mgr, data):
    status, _, additional = mgr.forward_open(data)
    assert status == cip.CONNECTION_FAILURE
    assert mgr.connections == {}
    return additional


def test_class1_exclusive_owner_opens():
    mgr, opened = manager()
    status, reply, _ = mgr.forward_open(owner(ot_rpi=20000, to_rpi=30000, multiplier=1))
    assert status == cip.SUCCESS
    ot_cid, to_cid = struct.unpack_from("<II", reply, 0)
    assert struct.unpack_from("<II", reply, 16) == (20000, 30000)      # API = RPI
    connection = mgr.lookup(ot_cid)
    assert connection.kind == "exclusive" and opened == [connection]
    assert (connection.ot_size, connection.to_size, connection.timeout_multiplier) == (8, 4, 1)
    assert to_cid == 0x11111111


@pytest.mark.parametrize("ot_size", [2, 6])
def test_class1_input_only_with_heartbeat(ot_size):
    mgr, opened = manager()
    status, _, _ = mgr.forward_open(owner(path=io_path(config=102, consumed=198, produced=100),
                                          ot_ncp=ncp16(size=ot_size)))
    assert status == cip.SUCCESS
    assert opened[0].kind == "input_only"


def test_class1_two_input_only_connections_coexist_with_an_owner():
    mgr, _ = manager()
    assert mgr.forward_open(owner(serial=1))[0] == cip.SUCCESS
    for serial in (2, 3):
        data = owner(serial=serial, path=io_path(config=102, consumed=198, produced=100),
                     ot_ncp=ncp16(size=2))
        assert mgr.forward_open(data)[0] == cip.SUCCESS
    assert len(mgr.connections) == 3


def test_class1_rejects_change_of_state_trigger():
    assert rejected(manager()[0], owner(transport=0x11)) == [0x0103]


def test_class1_rejects_unknown_transport_class():
    assert rejected(manager()[0], owner(transport=0x02)) == [0x0103]


def test_class1_rejects_device_without_io():
    assert rejected(manager(io=None)[0], owner()) == [0x0117]


def test_class1_rejects_unknown_produced_point():
    data = owner(path=io_path(config=102, consumed=101, produced=99))
    assert rejected(manager()[0], data) == [0x0117]


def test_class1_rejects_unknown_consumed_point():
    data = owner(path=io_path(config=102, consumed=55, produced=100))
    assert rejected(manager()[0], data) == [0x0117]


def test_class1_rejects_wrong_config_instance():
    data = owner(path=io_path(config=7, consumed=101, produced=100))
    assert rejected(manager()[0], data) == [0x0118]


def test_class1_rejects_multicast():
    assert rejected(manager()[0], owner(to_ncp=ncp16(size=4, conn_type=1))) == [0x0108]


def test_class1_rejects_fast_rpi():
    assert rejected(manager()[0], owner(to_rpi=5000)) == [0x0111]


def test_class1_rejects_wrong_ot_size_with_expected_size():
    assert rejected(manager()[0], owner(ot_ncp=ncp16(size=4))) == [0x0109, 8]


def test_class1_rejects_wrong_to_size_with_expected_size():
    assert rejected(manager()[0], owner(to_ncp=ncp16(size=10))) == [0x0109, 4]


def test_class1_rejects_duplicate_triad():
    mgr, _ = manager()
    data = owner(path=io_path(config=102, consumed=198, produced=100), ot_ncp=ncp16(size=2))
    assert mgr.forward_open(data)[0] == cip.SUCCESS
    status, _, additional = mgr.forward_open(data)
    assert (status, additional) == (cip.CONNECTION_FAILURE, [0x0100])


def test_class1_rejects_second_exclusive_owner():
    mgr, _ = manager()
    assert mgr.forward_open(owner(serial=1))[0] == cip.SUCCESS
    status, _, additional = mgr.forward_open(owner(serial=2))
    assert (status, additional) == (cip.CONNECTION_FAILURE, [0x0106])


def test_forward_close_calls_on_close():
    mgr, opened = manager()
    closed = []
    mgr.on_close = lambda c, reason: closed.append((c, reason))
    mgr.forward_open(owner(serial=0x5A5A, vendor=1, orig_serial=7))
    mgr.forward_close(bytes([0x06, 0x9C]) + struct.pack("<HHI", 0x5A5A, 1, 7))
    assert closed == [(opened[0], "close")]


def test_class3_forward_open_unchanged():
    mgr, opened = manager()
    data = forward_open_data(path=bytes([0x20, 0x02, 0x24, 0x01]), transport=0xA3,
                             ot_ncp=ncp16(size=500, fixed=False), to_ncp=ncp16(size=500, fixed=False),
                             ot_rpi=2000, to_rpi=2000)
    status, reply, _ = mgr.forward_open(data)
    assert status == cip.SUCCESS
    assert mgr.lookup(struct.unpack_from("<I", reply)[0]).kind == "class3"


# --- I/O points from profiles -----------------------------------------------------

def test_profile_io_points_for_bundled_profiles():
    drive = build_device("drive").io
    assert (drive.output_instance, drive.input_instance, drive.config_instance) == (20, 70, 1)
    assert (drive.output_size, drive.input_size, drive.heartbeat_instance) == (4, 4, 198)
    block = build_device("io-block").io
    assert (block.output_instance, block.input_instance, block.config_instance) == (101, 100, 102)
    assert build_device("generic-adapter").io is None


def test_profile_io_overrides():
    device = build_device({"profile": "io-block", "io": {"config_instance": 5,
                                                         "heartbeat_instance": 199}})
    assert (device.io.config_instance, device.io.heartbeat_instance) == (5, 199)


def test_explicit_output_write_blocked_while_owned():
    device = build_device("io-block")
    path = cip.encode_path(cip.ASSEMBLY, 101, 3)
    request = cip.encode_mr_request(cip.SET_ATTRIBUTE_SINGLE, path, b"\x01\x02")
    device.owned_output = True
    assert cip.decode_mr_reply(device.handle(request))[1] == cip.DEVICE_STATE_CONFLICT
    device.owned_output = False
    assert cip.decode_mr_reply(device.handle(request))[1] == cip.SUCCESS


def test_write_outputs_and_read_inputs():
    device = build_device("io-block")
    device.owned_output = True
    device.write_outputs(b"\x12\x34")          # implicit path ignores the guard
    device.tick(0.01)
    assert device.read_inputs() == b"\x12\x34"
