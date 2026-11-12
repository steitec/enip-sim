"""The command line, exercised through ``main([...])`` against a running bench."""
from __future__ import annotations

import json
import struct

import pytest

from enip_sim.bench import Bench
from enip_sim.cli import main


@pytest.fixture
def bench():
    b = Bench.single("drive", name="pump")
    b.start(port=0, control_port=0)
    yield b
    b.stop()


def test_profiles_lists_bundled(capsys):
    assert main(["profiles"]) == 0
    out = capsys.readouterr().out
    assert "drive" in out and "io-block" in out and "generic-adapter" in out


def test_info_shows_identity(capsys):
    assert main(["info", "drive"]) == 0
    out = capsys.readouterr().out
    assert "enip-sim drive" in out


def test_read_attribute(capsys, bench):
    code = main(["read", "0x01", "1", "1", "--port", str(bench.port)])
    assert code == 0
    assert "0100" in capsys.readouterr().out.lower()        # vendor id 0x0001, LE


def test_write_then_read_assembly_runs_motor(capsys, bench):
    assert main(["write", "0x04", "20", "3", "0100f401", "--port", str(bench.port)]) == 0
    for _ in range(25):
        bench.device.tick(0.1)
    code = main(["assembly", "70", "--port", str(bench.port)])
    assert code == 0
    out = capsys.readouterr().out
    assert "running" in out.lower()


def test_inject_and_list_via_control(capsys, bench):
    assert main(["inject", "trip", "code=5", "--control-port", str(bench.control_port)]) == 0
    bench.device.tick(0.1)
    assert main(["list", "--control-port", str(bench.control_port)]) == 0
    assert "fault" in capsys.readouterr().out.lower() or bench.device.model.faulted


def test_identity_over_the_wire(capsys, bench):
    assert main(["identity", "--port", str(bench.port)]) == 0
    assert "enip-sim drive" in capsys.readouterr().out
