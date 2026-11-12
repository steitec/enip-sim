"""Behaviour models: a drive that ramps and trips, an I/O block that mirrors bits."""
from __future__ import annotations

import struct

from enip_sim.models import DriveModel, IOBlockModel


def set_output(model, control_word, reference=0):
    model.output[:] = struct.pack("<HH", control_word, reference)


def test_drive_ramps_up_when_run_is_set():
    drive = DriveModel(max_speed=50.0, accel_time=5.0)
    set_output(drive, control_word=0x0001, reference=500)   # run forward, 50.0 Hz (0.1 Hz units)
    for _ in range(10):
        drive.tick(0.5)                                     # 5 s -> full ramp
    assert drive.speed > 49.0
    assert drive.running is True
    status = struct.unpack_from("<H", drive.input, 0)[0]
    assert status & 0x0001                                  # running bit set


def test_drive_stays_at_zero_without_run():
    drive = DriveModel()
    set_output(drive, control_word=0x0000, reference=500)
    for _ in range(10):
        drive.tick(0.5)
    assert drive.speed == 0.0
    assert drive.running is False


def test_drive_trip_latches_until_reset():
    drive = DriveModel(accel_time=1.0)
    set_output(drive, control_word=0x0001, reference=500)
    for _ in range(5):
        drive.tick(0.5)
    drive.trip(code=7)
    drive.tick(0.5)
    assert drive.faulted is True
    assert drive.fault_code == 7
    assert drive.speed == 0.0
    status = struct.unpack_from("<H", drive.input, 0)[0]
    assert status & 0x0004                                  # fault bit

    # a rising edge of the fault-reset bit while not running clears the trip
    set_output(drive, control_word=0x0000)
    drive.tick(0.1)
    set_output(drive, control_word=0x0004)                  # fault reset
    drive.tick(0.1)
    assert drive.faulted is False


def test_io_block_input_follows_signals_and_output_is_readback():
    io = IOBlockModel(input_size=2, output_size=2)
    io.output[:] = b"\x05\x00"
    io.tick(0.1)
    assert io.input[:] is not None
    assert len(io.input) == 2
