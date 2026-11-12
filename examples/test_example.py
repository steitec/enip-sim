"""Examples of testing a scanner against enip-sim, using the pytest plugin.

Run with:  pytest examples/test_example.py

The plugin (installed with enip-sim) provides ``enip_bench`` (one drive on a free port)
and ``enip_bench_factory`` (start more benches: a profile name, a dict, or a bench file).
"""
from __future__ import annotations

import struct
import time
from pathlib import Path

from enip_sim import cip

HERE = Path(__file__).parent


def run_command(speed_ref: int, *, reverse: bool = False) -> bytes:
    control = 0x0002 if reverse else 0x0001
    return struct.pack("<HH", control, speed_ref)


def test_drive_reaches_its_reference(enip_bench):
    """Write the output assembly, wait for the ramp, read the input assembly back."""
    with enip_bench.client() as client:
        client.set_attribute_single(cip.ASSEMBLY, 20, 3, run_command(250))   # 25.0 Hz
        time.sleep(3.0)                          # default ramp is 10 Hz/s
        status, speed = struct.unpack("<HH", client.get_attribute_single(cip.ASSEMBLY, 70, 3))
    assert status & 0x0001                      # running
    assert status & 0x0008                      # at reference
    assert 245 <= speed <= 250                  # ~25.0 Hz in 0.1 Hz units


def test_a_trip_shows_up_in_the_status_word(enip_bench):
    with enip_bench.client() as client:
        client.set_attribute_single(cip.ASSEMBLY, 20, 3, run_command(500))
        enip_bench.inject("trip", code=3)
        time.sleep(0.3)
        status, speed = struct.unpack("<HH", client.get_attribute_single(cip.ASSEMBLY, 70, 3))
    assert status & 0x0004                      # faulted
    assert speed == 0


def test_a_custom_bench_file(enip_bench_factory):
    bench = enip_bench_factory(str(HERE / "conveyor.yaml"))
    with bench.client() as client:
        name = client.list_identity()["product_name"]
    assert name == "enip-sim conveyor drive"


def test_an_io_block_mirrors_its_outputs(enip_bench_factory):
    bench = enip_bench_factory("io-block")
    with bench.client() as client:
        client.set_attribute_single(cip.ASSEMBLY, 101, 3, b"\x2a\x00")
        time.sleep(0.2)
        inputs = client.get_attribute_single(cip.ASSEMBLY, 100, 3)
    assert struct.unpack("<H", inputs)[0] == 0x2A
