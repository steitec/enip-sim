"""The JSON control channel of a running bench."""
from __future__ import annotations

import pytest

from enip_sim.bench import Bench
from enip_sim.control import send_control


@pytest.fixture
def bench():
    b = Bench.single("drive", name="pump")
    b.start(port=0, control_port=0)
    yield b
    b.stop()


def _call(bench, request):
    return send_control(request, host=bench.control_host, port=bench.control_port)


def test_inject_and_get(bench):
    assert _call(bench, {"inject": "trip", "params": {"code": 4}})["ok"] is True
    bench.device.tick(0.1)
    values = _call(bench, {"get": True})
    assert values["ok"] is True
    assert values["values"]["fault_code"] == 4


def test_clear(bench):
    _call(bench, {"inject": "trip"})
    bench.device.tick(0.1)
    assert _call(bench, {"clear": True})["ok"] is True
    assert bench.device.model.faulted is False


def test_list(bench):
    _call(bench, {"inject": "delay", "params": {"seconds": 0.1}})
    answer = _call(bench, {"list": True})
    assert "pump" in answer["devices"]
    assert "delay" in answer["devices"]["pump"]["faults"]


def test_unknown_fault_reports_error(bench):
    answer = _call(bench, {"inject": "explode"})
    assert answer["ok"] is False
    assert "explode" in answer["error"]
