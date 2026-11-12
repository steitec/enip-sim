"""The Bench: a device, its server and (optionally) a control channel, started together."""
from __future__ import annotations

import struct

import pytest

from enip_sim import cip
from enip_sim.bench import Bench, BenchError


def test_single_starts_and_serves():
    bench = Bench.single("drive", name="pump")
    bench.start(port=0)
    try:
        with bench.client() as c:
            data = c.get_attribute_single(cip.IDENTITY, 1, 7)
        assert data[1:].startswith(b"enip-sim")
    finally:
        bench.stop()


def test_from_dict_with_base_profile_override():
    bench = Bench.from_dict({"profile": "drive", "name": "fast",
                             "model": {"accel_time": 1.0}})
    assert bench.device.model.accel_time == 1.0
    assert bench.device.name == "fast"


def test_inject_through_bench():
    bench = Bench.single("drive")
    bench.start(port=0)
    try:
        bench.inject("trip", code=9)
        bench.device.tick(0.1)
        assert bench.device.model.faulted is True
        assert bench.variables()["fault_code"] == 9
    finally:
        bench.stop()


def test_unknown_profile_raises_bench_error():
    with pytest.raises(BenchError):
        Bench.single("nope")
