"""The EtherNet/IP TCP server: session registration and explicit messaging."""
from __future__ import annotations

import socket

import pytest

from enip_sim import cip
from enip_sim import encapsulation as enc
from enip_sim import objects as obj
from enip_sim.device import Device
from enip_sim.server import Server
from enip_helpers import exchange as _exchange
from enip_helpers import recv_packet as _recv_packet


@pytest.fixture
def server():
    identity = obj.identity_object(vendor_id=0x004B, device_type=0x02, product_code=0x1234,
                                   major=2, minor=7, status=0x0030, serial=0x0001,
                                   product_name="enip-sim test")
    assembly = obj.assembly_object(instance=100, size=4, data=b"\x01\x02\x03\x04")
    device = Device("dev", [identity, assembly])
    srv = Server(device, host="127.0.0.1", port=0)
    port = srv.start()
    yield port
    srv.stop()


def test_register_session_assigns_handle(server):
    header, data = _exchange(server, enc.REGISTER_SESSION, data=bytes([1, 0, 0, 0]))
    assert header.command == enc.REGISTER_SESSION
    assert header.session != 0
    assert data == bytes([1, 0, 0, 0])


def test_get_identity_vendor_id_over_rr_data(server):
    request = cip.encode_mr_request(cip.GET_ATTRIBUTE_SINGLE, cip.encode_path(0x01, 1, 1))
    header, body = _exchange(server, enc.SEND_RR_DATA, data=enc.encode_rr_data(request))
    assert header.status == enc.STATUS_SUCCESS
    cip_reply, _ = enc.decode_rr_data(body)
    service, status, _, payload = cip.decode_mr_reply(cip_reply)
    assert status == cip.SUCCESS
    assert payload == bytes.fromhex("4b00")


def test_rr_data_without_session_is_rejected(server):
    request = cip.encode_mr_request(cip.GET_ATTRIBUTE_SINGLE, cip.encode_path(0x01, 1, 1))
    with socket.create_connection(("127.0.0.1", server), timeout=2) as sock:
        sock.sendall(enc.encode(enc.SEND_RR_DATA, session=0x99999999,
                                data=enc.encode_rr_data(request)))
        header, _ = enc.decode(_recv_packet(sock))
    assert header.status == enc.STATUS_INVALID_SESSION


def test_list_identity_reports_product_name(server):
    header, body = _exchange(server, enc.LIST_IDENTITY)
    items = enc.decode_cpf(body)
    assert items and items[0][0] == enc.ITEM_LIST_IDENTITY
    assert b"enip-sim test" in items[0][1]
