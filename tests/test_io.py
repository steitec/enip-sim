"""The UDP I/O engine: cyclic Class 1 exchange (0.2.0)."""
from __future__ import annotations

import socket
import threading
import time

import pytest

from enip_sim import faults
from enip_sim.connection import ConnectionManager
from enip_sim.io import IOEngine, decode_io_packet, encode_io_packet
from enip_sim.profile import build_device
from enip_helpers import forward_open_data, io_path, ncp16, wait_until


class Originator:
    """A raw UDP originator: collects T->O packets, sends O->T packets."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.05)
        self.port = self.sock.getsockname()[1]
        self.packets = []            # (monotonic time, IOPacket)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                data, _ = self.sock.recvfrom(2048)
            except (socket.timeout, OSError):
                continue
            self.packets.append((time.monotonic(), decode_io_packet(data, with_header=False)))

    def send(self, engine_port, cid, data, *, run=True, seq=1):
        self.sock.sendto(encode_io_packet(cid, seq, seq, data, run=run),
                         ("127.0.0.1", engine_port))

    def close(self):
        self._stop.set()
        self._thread.join(timeout=2)
        self.sock.close()


@pytest.fixture
def rig():
    device = build_device("io-block")
    manager = ConnectionManager(device_io=lambda: device.io)
    engine = IOEngine(device, manager, host="127.0.0.1", port=0)
    origin = Originator()

    def opened(connection):
        connection.peer = ("127.0.0.1", origin.port)
        engine.add(connection)

    manager.on_open = opened
    manager.on_close = engine.remove
    engine.start()

    def open_io(**kw):
        args = dict(path=io_path(config=102, consumed=101, produced=100),
                    ot_ncp=ncp16(size=8), to_ncp=ncp16(size=4), ot_rpi=20000, to_rpi=20000)
        args.update(kw)
        status, reply, add = manager.forward_open(forward_open_data(**args))
        assert status == 0, add
        return manager.lookup(int.from_bytes(reply[:4], "little"))

    yield device, manager, engine, origin, open_io
    engine.stop()
    origin.close()


def test_encode_decode_roundtrip_header_and_modeless():
    packet = decode_io_packet(encode_io_packet(0x1234, 7, 3, b"\xaa\xbb", run=True),
                              with_header=True)
    assert (packet.cid, packet.enc_seq, packet.cip_seq, packet.header, packet.data) == \
        (0x1234, 7, 3, 1, b"\xaa\xbb")
    modeless = decode_io_packet(encode_io_packet(1, 2, 3, b"\x01"), with_header=False)
    assert (modeless.header, modeless.data) == (None, b"\x01")


def test_decode_rejects_garbage():
    from enip_sim.encapsulation import EncapsulationError
    with pytest.raises(EncapsulationError):
        decode_io_packet(b"\x01\x02\x03", with_header=False)


def test_io_produces_at_rpi(rig):
    device, manager, engine, origin, open_io = rig
    connection = open_io(to_rpi=50000, multiplier=7)
    origin.send(engine.port, connection.ot_cid, b"\x00\x00")
    wait_until(lambda: origin.packets)
    start = len(origin.packets)
    time.sleep(0.5)
    count = len(origin.packets) - start
    assert 4 <= count <= 13, count          # 10 nominal; slack for slow CI runners
    assert all(p.cid == connection.to_cid for _, p in origin.packets)


def test_io_cip_sequence_increments(rig):
    device, manager, engine, origin, open_io = rig
    open_io(multiplier=7)
    wait_until(lambda: len(origin.packets) >= 3)
    seqs = [p.enc_seq for _, p in origin.packets[:3]]
    assert seqs[1] == seqs[0] + 1 and seqs[2] == seqs[1] + 1
    cips = [p.cip_seq for _, p in origin.packets[:3]]
    assert cips[1] == (cips[0] + 1) & 0xFFFF


def test_io_run_writes_outputs(rig):
    device, manager, engine, origin, open_io = rig
    connection = open_io(multiplier=7)
    origin.send(engine.port, connection.ot_cid, b"\x55\xaa", run=True)
    wait_until(lambda: bytes(device.model.output) == b"\x55\xaa")
    device.tick(0.01)
    wait_until(lambda: any(p.data == b"\x55\xaa" for _, p in origin.packets))
    assert connection.run


def test_io_idle_holds_outputs(rig):
    device, manager, engine, origin, open_io = rig
    connection = open_io(multiplier=7)
    origin.send(engine.port, connection.ot_cid, b"\x01\x02", run=True, seq=1)
    wait_until(lambda: bytes(device.model.output) == b"\x01\x02")
    origin.send(engine.port, connection.ot_cid, b"\x09\x09", run=False, seq=2)
    wait_until(lambda: not connection.run)
    assert bytes(device.model.output) == b"\x01\x02"


def test_io_owner_blocks_explicit_writes_until_closed(rig):
    device, manager, engine, origin, open_io = rig
    connection = open_io(multiplier=7)
    assert device.owned_output
    manager.close(connection, "close")
    assert not device.owned_output


def test_io_timeout_closes_and_zeroes_outputs(rig):
    device, manager, engine, origin, open_io = rig
    closed = []
    manager.on_close = lambda c, reason: (closed.append(reason), engine.remove(c, reason))
    connection = open_io(multiplier=0, ot_rpi=20000)        # timeout 4 x 20 ms
    origin.send(engine.port, connection.ot_cid, b"\x07\x07")
    wait_until(lambda: bytes(device.model.output) == b"\x07\x07")
    wait_until(lambda: closed, timeout=2.0)
    assert closed == ["timeout"]
    assert manager.connections == {}
    assert bytes(device.model.output) == b"\x00\x00"
    assert not device.owned_output
    count = len(origin.packets)
    time.sleep(0.1)
    assert len(origin.packets) == count                      # production stopped


def test_io_keeps_connection_while_originator_sends(rig):
    device, manager, engine, origin, open_io = rig
    # timeout 16 x 20 ms = 320 ms; keep sending for at least twice that. (A tighter
    # multiplier flakes on macOS runners, where 20 ms sleeps were seen to take ~100 ms.)
    connection = open_io(multiplier=2, ot_rpi=20000)
    start = time.monotonic()
    seq = 0
    while time.monotonic() - start < 0.7:
        seq += 1
        origin.send(engine.port, connection.ot_cid, b"\x00\x00", seq=seq)
        time.sleep(0.02)
    assert connection.ot_cid in manager.connections


def test_io_loss_skips_every_nth(rig):
    device, manager, engine, origin, open_io = rig
    device.inject("io_loss", every=2)
    open_io(multiplier=7)
    wait_until(lambda: len(origin.packets) >= 4)
    seqs = [p.enc_seq for _, p in origin.packets[:4]]
    gaps = [b - a for a, b in zip(seqs, seqs[1:])]
    assert gaps == [2, 2, 2]


def test_io_loss_validates_every():
    with pytest.raises(faults.FaultError):
        faults.FaultSet().inject("io_loss", every=1)


def test_connection_timeout_fault_silences_device(rig):
    device, manager, engine, origin, open_io = rig
    open_io(multiplier=7)
    wait_until(lambda: origin.packets)
    device.inject("connection_timeout")
    time.sleep(0.05)
    count = len(origin.packets)
    time.sleep(0.15)
    assert len(origin.packets) <= count + 1
    device.clear("connection_timeout")
    wait_until(lambda: len(origin.packets) > count + 2)


def test_io_ignores_packets_from_unknown_address(rig):
    device, manager, engine, origin, open_io = rig
    connection = open_io(multiplier=7)
    connection.peer = ("127.0.0.2", origin.port)       # the scanner "is" elsewhere
    origin.send(engine.port, connection.ot_cid, b"\x33\x33")
    time.sleep(0.1)
    assert bytes(device.model.output) == b"\x00\x00"


def test_io_ignores_unknown_connection_id(rig):
    device, manager, engine, origin, open_io = rig
    open_io(multiplier=7)
    origin.send(engine.port, 0x0BADC0DE, b"\x44\x44")
    time.sleep(0.1)
    assert bytes(device.model.output) == b"\x00\x00"


def test_io_survives_garbage_datagram(rig):
    device, manager, engine, origin, open_io = rig
    connection = open_io(multiplier=7)
    origin.sock.sendto(b"\xff" * 5, ("127.0.0.1", engine.port))
    origin.send(engine.port, connection.ot_cid, b"\x0f\xf0")
    wait_until(lambda: bytes(device.model.output) == b"\x0f\xf0")


def test_input_only_connection_produces_without_owning(rig):
    device, manager, engine, origin, open_io = rig
    connection = open_io(path=io_path(config=102, consumed=198, produced=100),
                         ot_ncp=ncp16(size=2), multiplier=7)
    assert connection.kind == "input_only" and not device.owned_output
    wait_until(lambda: origin.packets)


def test_stop_with_open_io_connection():
    device = build_device("io-block")
    manager = ConnectionManager(device_io=lambda: device.io)
    engine = IOEngine(device, manager, host="127.0.0.1", port=0)
    origin = Originator()
    manager.on_open = lambda c: (setattr(c, "peer", ("127.0.0.1", origin.port)), engine.add(c))
    engine.start()
    manager.forward_open(forward_open_data(path=io_path(config=102, consumed=101, produced=100),
                                           ot_ncp=ncp16(size=8), to_ncp=ncp16(size=4),
                                           ot_rpi=10000, to_rpi=10000, multiplier=7))
    wait_until(lambda: origin.packets)
    engine.stop()
    assert not any(t.is_alive() for t in engine.threads)
    count = len(origin.packets)
    time.sleep(0.05)
    assert len(origin.packets) == count
    origin.close()
