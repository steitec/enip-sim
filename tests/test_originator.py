"""The Class 1 originator: open_io from Python, the bench and the CLI (0.2.0)."""
from __future__ import annotations

import struct

import pytest

from enip_sim.bench import Bench
from enip_sim.cli import main
from enip_sim.client import ForwardOpenError
from enip_sim.originator import IOConnection


@pytest.fixture
def block():
    bench = Bench.single("io-block")
    bench.start(port=0)
    yield bench
    bench.stop()


@pytest.fixture
def drive():
    bench = Bench.from_dict({"profile": "drive", "model": {"accel_time": 0.5}})
    bench.start(port=0)
    yield bench
    bench.stop()


def test_open_io_against_io_block_loopback(block):
    with block.client() as client:
        with client.open_io(output_instance=101, input_instance=100, config_instance=102,
                            output_size=2, input_size=2, rpi_ms=20) as io:
            assert isinstance(io, IOConnection)
            io.set_outputs(b"\x55\xaa")
            io.run()
            assert io.wait_for_inputs(lambda data: data == b"\x55\xaa") == b"\x55\xaa"
            assert io.packets_received > 0 and io.last_input_time is not None


def test_open_io_drive_ramps(drive):
    with drive.open_io(rpi_ms=20) as io:
        io.set_outputs(struct.pack("<HH", 0x0001, 250))          # run forward, 25.0 Hz
        io.run()
        io.wait_for_inputs(lambda d: struct.unpack("<HH", d)[1] > 50, timeout=3.0)


def test_open_io_idle_does_not_start_the_drive(drive):
    with drive.open_io(rpi_ms=20) as io:
        io.set_outputs(struct.pack("<HH", 0x0001, 250))
        io.idle()
        io.wait_for_inputs(lambda d: d is not None)
        assert struct.unpack("<HH", drive.device.model.output) == (0, 0)


def test_open_io_rejected_raises_with_extended_status(block):
    with block.client() as client:
        with pytest.raises(ForwardOpenError) as caught:
            client.open_io(output_instance=101, input_instance=100, config_instance=102,
                           output_size=3, input_size=2)
    assert caught.value.extended == 0x0109
    assert "0x0109" in str(caught.value)


def test_open_io_input_only(block):
    with block.open_io(rpi_ms=20, input_only=True) as io:
        io.wait_for_inputs(lambda d: d is not None)
        assert not block.device.owned_output


def test_open_io_large_forward_open(block):
    with block.open_io(rpi_ms=20, large=True) as io:
        io.wait_for_inputs(lambda d: d is not None)


def test_close_sends_forward_close(block):
    io = block.open_io(rpi_ms=20)
    io.wait_for_inputs(lambda d: d is not None)
    io.close()
    assert block.device.owned_output is False
    assert all(not c.is_io for c in block._server.connections.connections.values())


def test_bench_open_io_defaults(block):
    with block.open_io() as io:
        assert io.rpi_ms == 50
        assert io.wait_for_inputs(lambda d: d is not None) == b"\x00\x00"


def test_bench_open_io_without_io_points():
    bench = Bench.single("generic-adapter")
    bench.start(port=0)
    try:
        with pytest.raises(ValueError):
            bench.open_io()
    finally:
        bench.stop()


def test_cli_io_prints_inputs(block, capsys):
    code = main(["io", "--port", str(block.port), "--profile", "io-block", "--out", "a55a",
                 "--rpi", "20", "--seconds", "1.5"])
    out = capsys.readouterr().out
    assert code == 0
    assert "a55a" in out


def test_cli_io_decodes_drive_status(drive, capsys):
    code = main(["io", "--port", str(drive.port), "--out", "0100fa00", "--rpi", "20",
                 "--seconds", "0.6"])
    out = capsys.readouterr().out
    assert code == 0
    assert "running" in out


def test_cli_io_rejection_exits_1(block, capsys):
    code = main(["io", "--port", str(block.port), "--profile", "io-block", "--rpi", "5",
                 "--seconds", "0.2"])
    assert code == 1
    assert "0x0111" in capsys.readouterr().err


def test_cli_serve_parser_has_io_port():
    from enip_sim.cli import build_parser
    args = build_parser().parse_args(["serve", "io-block", "--io-port", "0"])
    assert args.io_port == 0
