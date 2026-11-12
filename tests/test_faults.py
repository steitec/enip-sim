"""Fault injection: the FaultSet mechanics and their effect on a device."""
from __future__ import annotations

import pytest

from enip_sim import cip
from enip_sim import profile as prof
from enip_sim.faults import FaultError, FaultSet


def test_one_shot_fault_fires_once():
    faults = FaultSet()
    faults.inject("no_response")
    assert faults.take("no_response") is not None
    assert faults.take("no_response") is None


def test_counted_fault_fires_n_times():
    faults = FaultSet()
    faults.inject("no_response", count=2)
    assert faults.take("no_response") is not None
    assert faults.take("no_response") is not None
    assert faults.take("no_response") is None


def test_persistent_fault_keeps_firing():
    faults = FaultSet()
    faults.inject("delay", seconds=0.1)
    assert faults.take("delay")["seconds"] == 0.1
    assert faults.take("delay")["seconds"] == 0.1


def test_clear_removes_fault():
    faults = FaultSet()
    faults.inject("delay", seconds=0.1)
    faults.clear("delay")
    assert faults.take("delay") is None


def test_unknown_fault_rejected():
    with pytest.raises(FaultError):
        FaultSet().inject("explode")


def test_unknown_parameter_rejected():
    with pytest.raises(FaultError):
        FaultSet().inject("delay", seconds=0.1, color="red")


def test_inject_trip_trips_the_drive_model():
    device = prof.build_device("drive")
    device.inject("trip", code=5)
    device.tick(0.1)
    assert device.model.faulted is True
    assert device.model.fault_code == 5
    device.clear("trip")
    assert device.model.faulted is False


def test_bad_status_makes_reads_fail():
    device = prof.build_device("generic-adapter")
    device.inject("bad_status", status=cip.RESOURCE_UNAVAILABLE)
    reply = device.handle(cip.encode_mr_request(cip.GET_ATTRIBUTE_SINGLE,
                                                cip.encode_path(0x01, 1, 1)))
    _, status, _, _ = cip.decode_mr_reply(reply)
    assert status == cip.RESOURCE_UNAVAILABLE
