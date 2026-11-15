"""Class 1 connections through the TCP server (0.2.0)."""
from __future__ import annotations

import socket
import struct
import time

import pytest

from enip_sim import cip
from enip_sim import encapsulation as enc
from enip_sim.bench import Bench
from enip_sim.io import decode_io_packet, encode_io_packet
from enip_sim.profile import build_device
from enip_sim.server import Server
from enip_helpers import (forward_open_data, io_path, ncp16, recv_packet, register_session,
                          wait_until)


class Scanner:
    """TCP session + UDP socket, like a scanner."""

    def __init__(self, server: Server):
        self.server = server
        self.tcp = socket.create_connection(("127.0.0.1", server.port), timeout=2)
        self.session = register_session(self.tcp)
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.bind(("127.0.0.1", 0))
        self.udp.settimeout(1.0)
        self.udp_port = self.udp.getsockname()[1]

    def forward_open(self, data, *, t2o_item=True, service=cip.FORWARD_OPEN):
        request = cip.encode_mr_request(service, cip.encode_path(cip.CONNECTION_MANAGER, 1), data)
        items = [(enc.ITEM_SOCKADDR_T2O, enc.encode_sockaddr("0.0.0.0", self.udp_port))] \
            if t2o_item else []
        self.tcp.sendall(enc.encode(enc.SEND_RR_DATA, session=self.session,
                                    data=enc.encode_rr_data(request, extra_items=items)))
        _, body = enc.decode(recv_packet(self.tcp))
        reply, _, reply_items = enc.decode_rr_items(body)
        _, status, additional, rdata = cip.decode_mr_reply(reply)
        return status, additional, rdata, dict(reply_items)

    def forward_close(self, serial=0x1234, vendor=0x004B, orig_serial=0xDEADBEEF):
        data = bytes([0x0A, 0x0E]) + struct.pack("<HHI", serial, vendor, orig_serial) \
            + bytes([0x00, 0x00])
        request = cip.encode_mr_request(cip.FORWARD_CLOSE,
                                        cip.encode_path(cip.CONNECTION_MANAGER, 1), data)
        self.tcp.sendall(enc.encode(enc.SEND_RR_DATA, session=self.session,
                                    data=enc.encode_rr_data(request)))
        recv_packet(self.tcp)

    def receive(self):
        data, _ = self.udp.recvfrom(2048)
        return decode_io_packet(data, with_header=False)

    def send(self, cid, payload, *, run=True, seq=1):
        self.udp.sendto(encode_io_packet(cid, seq, seq, payload, run=run),
                        ("127.0.0.1", self.server.io_port))

    def close(self):
        self.tcp.close()
        self.udp.close()


@pytest.fixture
def served():
    server = Server(build_device("io-block"), host="127.0.0.1", port=0)
    server.start()
    scanner = Scanner(server)
    yield server, scanner
    scanner.close()
    server.stop()


def owner(**kw):
    args = dict(path=io_path(config=102, consumed=101, produced=100),
                ot_ncp=ncp16(size=8), to_ncp=ncp16(size=4), multiplier=7)
    args.update(kw)
    return forward_open_data(**args)


def test_sockaddr_roundtrip():
    item = enc.encode_sockaddr("192.168.1.20", 2222)
    assert len(item) == 16
    assert item[:2] == b"\x00\x02" and item[2:4] == b"\x08\xae"
    assert enc.decode_sockaddr(item) == ("192.168.1.20", 2222)


def test_forward_open_over_tcp_starts_io(served):
    server, scanner = served
    status, additional, rdata, _ = scanner.forward_open(owner())
    assert status == cip.SUCCESS, additional
    to_cid = struct.unpack_from("<I", rdata, 4)[0]
    packet = scanner.receive()
    assert packet.cid == to_cid


def test_outputs_reach_the_device_and_loop_back(served):
    server, scanner = served
    _, _, rdata, _ = scanner.forward_open(owner())
    ot_cid = struct.unpack_from("<I", rdata, 0)[0]
    scanner.send(ot_cid, b"\x5a\xa5")

    def looped():
        scanner.send(ot_cid, b"\x5a\xa5")
        return scanner.receive().data == b"\x5a\xa5"
    wait_until(looped)


def test_reply_has_o2t_sockaddr_when_not_2222(served):
    server, scanner = served
    assert server.io_port != 2222
    _, _, _, items = scanner.forward_open(owner())
    assert enc.decode_sockaddr(items[enc.ITEM_SOCKADDR_O2T])[1] == server.io_port


def test_without_t2o_item_production_goes_to_2222(served):
    server, scanner = served
    status, _, rdata, _ = scanner.forward_open(owner(), t2o_item=False)
    assert status == cip.SUCCESS
    ot_cid = struct.unpack_from("<I", rdata, 0)[0]
    assert server.connections.lookup(ot_cid).peer == ("127.0.0.1", 2222)


def test_large_forward_open_starts_io(served):
    from enip_helpers import ncp32
    server, scanner = served
    data = owner(large=True, ot_ncp=ncp32(size=8), to_ncp=ncp32(size=4))
    status, additional, _, _ = scanner.forward_open(data, service=cip.LARGE_FORWARD_OPEN)
    assert status == cip.SUCCESS, additional
    scanner.receive()


def test_rejected_forward_open_answers_extended_status(served):
    server, scanner = served
    status, additional, _, _ = scanner.forward_open(owner(ot_ncp=ncp16(size=5)))
    assert (status, additional) == (cip.CONNECTION_FAILURE, [0x0109, 8])


def test_forward_close_stops_io_and_holds_outputs(served):
    server, scanner = served
    _, _, rdata, _ = scanner.forward_open(owner())
    ot_cid = struct.unpack_from("<I", rdata, 0)[0]
    scanner.send(ot_cid, b"\x11\x22")
    wait_until(lambda: bytes(server.device.model.output) == b"\x11\x22")
    scanner.forward_close()
    assert server.connections.connections == {}
    time.sleep(0.1)
    scanner.udp.settimeout(0.01)
    while True:                                  # drain what was in flight
        try:
            scanner.udp.recvfrom(2048)
        except socket.timeout:
            break
    time.sleep(0.15)
    with pytest.raises(socket.timeout):
        scanner.udp.recvfrom(2048)
    assert bytes(server.device.model.output) == b"\x11\x22"
    assert not server.device.owned_output


def test_reopen_after_timeout(served):
    server, scanner = served
    status, _, _, _ = scanner.forward_open(owner(multiplier=0, ot_rpi=20000))
    assert status == cip.SUCCESS
    wait_until(lambda: server.connections.connections == {})          # no O->T: timeout
    status, additional, _, _ = scanner.forward_open(owner(multiplier=0, ot_rpi=20000))
    assert status == cip.SUCCESS, additional


def test_class3_connected_messaging_still_works(served):
    server, scanner = served
    status, _, rdata, _ = scanner.forward_open(
        forward_open_data(path=bytes([0x20, 0x02, 0x24, 0x01]), transport=0xA3,
                          ot_ncp=ncp16(size=500, fixed=False),
                          to_ncp=ncp16(size=500, fixed=False)))
    assert status == cip.SUCCESS


def test_bench_io_port_ephemeral():
    bench = Bench.single("io-block")
    bench.start(port=0)
    try:
        assert bench.io_port not in (None, 0, 2222)
    finally:
        bench.stop()


def test_server_describe_mentions_udp():
    server = Server(build_device("io-block"), host="127.0.0.1", port=0)
    server.start()
    try:
        assert f"udp {server.io_port}" in server.describe()
    finally:
        server.stop()
