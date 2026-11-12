"""CIP objects: attribute access and the standard services."""
from __future__ import annotations

from enip_sim import cip
from enip_sim import objects as obj


def make_identity():
    return obj.identity_object(vendor_id=0x004B, device_type=0x02, product_code=0x1234,
                               major=2, minor=7, status=0x0030, serial=0x12345678,
                               product_name="enip-sim drive")


def test_get_attribute_single_uint():
    identity = make_identity()
    status, data, _ = identity.service(cip.GET_ATTRIBUTE_SINGLE,
                                       cip.decode_path(cip.encode_path(0x01, 1, 1)), b"")
    assert status == cip.SUCCESS
    assert data == bytes.fromhex("4b00")   # vendor id 0x004B, little-endian UINT


def test_get_attribute_single_short_string():
    identity = make_identity()
    status, data, _ = identity.service(cip.GET_ATTRIBUTE_SINGLE,
                                       cip.decode_path(cip.encode_path(0x01, 1, 7)), b"")
    assert status == cip.SUCCESS
    assert data == bytes([len("enip-sim drive")]) + b"enip-sim drive"


def test_get_attribute_single_unknown_attribute():
    identity = make_identity()
    status, data, _ = identity.service(cip.GET_ATTRIBUTE_SINGLE,
                                       cip.decode_path(cip.encode_path(0x01, 1, 99)), b"")
    assert status == cip.ATTRIBUTE_NOT_SUPPORTED
    assert data == b""


def test_get_attributes_all_identity_order():
    identity = make_identity()
    status, data, _ = identity.service(cip.GET_ATTRIBUTES_ALL,
                                       cip.decode_path(cip.encode_path(0x01, 1)), b"")
    assert status == cip.SUCCESS
    expected = (bytes.fromhex("4b00")         # vendor id
                + bytes.fromhex("0200")       # device type
                + bytes.fromhex("3412")       # product code
                + bytes([2, 7])               # revision major, minor
                + bytes.fromhex("3000")       # status
                + bytes.fromhex("78563412")   # serial
                + bytes([len("enip-sim drive")]) + b"enip-sim drive")
    assert data == expected


def test_set_attribute_single_read_only_is_refused():
    identity = make_identity()
    status, _, _ = identity.service(cip.SET_ATTRIBUTE_SINGLE,
                                    cip.decode_path(cip.encode_path(0x01, 1, 1)),
                                    bytes.fromhex("ffff"))
    assert status == cip.ATTRIBUTE_NOT_SETTABLE


def test_service_not_supported():
    identity = make_identity()
    status, _, _ = identity.service(0x42,
                                    cip.decode_path(cip.encode_path(0x01, 1)), b"")
    assert status == cip.SERVICE_NOT_SUPPORTED


def test_assembly_get_and_set():
    assembly = obj.assembly_object(instance=100, size=4)
    status, _, _ = assembly.service(cip.SET_ATTRIBUTE_SINGLE,
                                    cip.decode_path(cip.encode_path(0x04, 100, 3)),
                                    b"\x01\x02\x03\x04")
    assert status == cip.SUCCESS
    status, data, _ = assembly.service(cip.GET_ATTRIBUTE_SINGLE,
                                       cip.decode_path(cip.encode_path(0x04, 100, 3)), b"")
    assert status == cip.SUCCESS
    assert data == b"\x01\x02\x03\x04"


def test_assembly_rejects_wrong_size_write():
    assembly = obj.assembly_object(instance=100, size=4)
    status, _, _ = assembly.service(cip.SET_ATTRIBUTE_SINGLE,
                                    cip.decode_path(cip.encode_path(0x04, 100, 3)),
                                    b"\x01\x02")
    assert status == cip.NOT_ENOUGH_DATA
