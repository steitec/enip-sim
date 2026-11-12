"""The pytest plugin's fixtures."""
from __future__ import annotations

import struct

from enip_sim import cip


def test_enip_bench_default_is_a_drive(enip_bench):
    with enip_bench.client() as c:
        name = c.get_attribute_single(cip.IDENTITY, 1, 7)
    assert name[1:].startswith(b"enip-sim")


def test_enip_bench_injects_faults(enip_bench):
    enip_bench.inject("trip", code=3)
    enip_bench.device.tick(0.1)
    assert enip_bench.variables()["fault_code"] == 3


def test_factory_starts_a_profile(enip_bench_factory):
    bench = enip_bench_factory("io-block")
    with bench.client() as c:
        c.set_attribute_single(cip.ASSEMBLY, 101, 3, b"\x05\x00")
        bench.device.tick(0.1)
        inputs = c.get_attribute_single(cip.ASSEMBLY, 100, 3)
    assert struct.unpack("<H", inputs)[0] == 5     # loopback mirrors the outputs


def test_factory_from_dict(enip_bench_factory):
    bench = enip_bench_factory({"profile": "drive", "name": "quick",
                                "model": {"accel_time": 1.0}})
    assert bench.device.name == "quick"
