"""Server-level faults over a real socket and the model ticking while served."""
from __future__ import annotations

import socket
import struct
import time

import pytest

from enip_sim import cip
from enip_sim import encapsulation as enc
from enip_sim import profile as prof
from enip_sim.server import Server
from enip_helpers import exchange as _exchange
from enip_helpers import recv_packet as _recv_packet
from enip_helpers import register_session as _session


@pytest.fixture
def running():
    device = prof.build_device("drive", name="pump")
    server = Server(device, host="127.0.0.1", port=0)
    port = server.start()
    yield device, port
    server.stop()


def _read_identity(port):
    request = cip.encode_mr_request(cip.GET_ATTRIBUTE_SINGLE, cip.encode_path(0x01, 1, 1))
    return _exchange(port, enc.SEND_RR_DATA, data=enc.encode_rr_data(request))


def test_no_response_makes_the_scanner_time_out(running):
    device, port = running
    device.inject("no_response")
    with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
        session = _session(sock)
        request = cip.encode_mr_request(cip.GET_ATTRIBUTE_SINGLE, cip.encode_path(0x01, 1, 1))
        sock.sendall(enc.encode(enc.SEND_RR_DATA, session=session,
                                data=enc.encode_rr_data(request)))
        sock.settimeout(0.5)
        with pytest.raises(socket.timeout):
            sock.recv(64)


def test_drop_connection_closes_the_socket(running):
    device, port = running
    device.inject("drop_connection")
    with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
        session = _session(sock)
        request = cip.encode_mr_request(cip.GET_ATTRIBUTE_SINGLE, cip.encode_path(0x01, 1, 1))
        sock.sendall(enc.encode(enc.SEND_RR_DATA, session=session,
                                data=enc.encode_rr_data(request)))
        sock.settimeout(2)
        assert sock.recv(64) == b""        # the server closed the connection


def test_delay_makes_the_reply_late(running):
    device, port = running
    device.inject("delay", seconds=0.3)
    start = time.monotonic()
    _read_identity(port)
    assert time.monotonic() - start >= 0.3


def test_model_runs_while_served(running):
    device, port = running
    out = device.model.OUTPUT_INSTANCE
    with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
        session = _session(sock)
        set_req = cip.encode_mr_request(cip.SET_ATTRIBUTE_SINGLE,
                                        cip.encode_path(0x04, out, 3),
                                        struct.pack("<HH", 0x0001, 500))
        sock.sendall(enc.encode(enc.SEND_RR_DATA, session=session,
                                data=enc.encode_rr_data(set_req)))
        _recv_packet(sock)
        time.sleep(1.0)                    # the server ticks the model in the background
    assert device.model.speed > 5.0
