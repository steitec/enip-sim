"""Device routing: a CIP request reaches the right object and comes back framed."""
from __future__ import annotations

from enip_sim import cip
from enip_sim import objects as obj
from enip_sim.device import Device


def make_device():
    identity = obj.identity_object(vendor_id=0x004B, device_type=0x02, product_code=1,
                                   major=1, minor=0, status=0x0030, serial=1,
                                   product_name="dev")
    assembly = obj.assembly_object(instance=100, size=2, data=b"\xaa\xbb")
    return Device("dev", [identity, assembly])


def test_handle_get_attribute_single():
    device = make_device()
    request = cip.encode_mr_request(cip.GET_ATTRIBUTE_SINGLE, cip.encode_path(0x01, 1, 1))
    reply = device.handle(request)
    service, status, _, data = cip.decode_mr_reply(reply)
    assert service == cip.GET_ATTRIBUTE_SINGLE
    assert status == cip.SUCCESS
    assert data == bytes.fromhex("4b00")


def test_handle_counts_requests():
    device = make_device()
    device.handle(cip.encode_mr_request(cip.GET_ATTRIBUTE_SINGLE, cip.encode_path(0x01, 1, 1)))
    device.handle(cip.encode_mr_request(cip.GET_ATTRIBUTE_SINGLE, cip.encode_path(0x04, 100, 3)))
    assert device.requests == 2


def test_handle_unknown_class_is_path_destination_unknown():
    device = make_device()
    request = cip.encode_mr_request(cip.GET_ATTRIBUTE_SINGLE, cip.encode_path(0x77, 1, 1))
    reply = device.handle(request)
    _, status, _, _ = cip.decode_mr_reply(reply)
    assert status == cip.PATH_DESTINATION_UNKNOWN


def test_handle_unknown_instance_is_path_destination_unknown():
    device = make_device()
    request = cip.encode_mr_request(cip.GET_ATTRIBUTE_SINGLE, cip.encode_path(0x04, 999, 3))
    reply = device.handle(request)
    _, status, _, _ = cip.decode_mr_reply(reply)
    assert status == cip.PATH_DESTINATION_UNKNOWN


def test_assembly_read_write_through_device():
    device = make_device()
    device.handle(cip.encode_mr_request(cip.SET_ATTRIBUTE_SINGLE,
                                        cip.encode_path(0x04, 100, 3), b"\x11\x22"))
    reply = device.handle(cip.encode_mr_request(cip.GET_ATTRIBUTE_SINGLE,
                                                cip.encode_path(0x04, 100, 3)))
    _, status, _, data = cip.decode_mr_reply(reply)
    assert status == cip.SUCCESS
    assert data == b"\x11\x22"
