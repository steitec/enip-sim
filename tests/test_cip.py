"""CIP: EPATH logical segments and the Message Router request/reply framing."""
from __future__ import annotations

import pytest

from enip_sim import cip


def test_encode_path_8bit_segments():
    # Identity (class 1), instance 1, attribute 1 -> three 8-bit logical segments
    assert cip.encode_path(0x01, 1, 1) == bytes.fromhex("20012401" "3001")


def test_encode_path_16bit_class():
    # TCP/IP Interface object is class 0xF5 but fits in 8 bits; a class >= 256 is 16-bit
    assert cip.encode_path(0x0100, 1) == bytes.fromhex("2100" "0001" "2401")


def test_decode_path_round_trip():
    path = cip.encode_path(0x04, 100, 3)
    parsed = cip.decode_path(path)
    assert parsed.class_id == 0x04
    assert parsed.instance == 100
    assert parsed.attribute == 3


def test_decode_path_rejects_unknown_segment():
    with pytest.raises(cip.CIPError):
        cip.decode_path(b"\x99\x01")


def test_mr_request_round_trip():
    request = cip.encode_mr_request(cip.GET_ATTRIBUTE_SINGLE, cip.encode_path(0x01, 1, 7))
    assert request == bytes.fromhex("0e03" "2001" "2401" "3007")
    service, path, data = cip.decode_mr_request(request)
    assert service == cip.GET_ATTRIBUTE_SINGLE
    assert cip.decode_path(path).attribute == 7
    assert data == b""


def test_mr_reply_round_trip():
    reply = cip.encode_mr_reply(cip.GET_ATTRIBUTE_SINGLE, cip.SUCCESS, data=b"\x34\x12")
    assert reply == bytes.fromhex("8e00" "0000" "3412")
    service, status, additional, data = cip.decode_mr_reply(reply)
    assert service == cip.GET_ATTRIBUTE_SINGLE   # reply bit stripped
    assert status == cip.SUCCESS
    assert additional == []
    assert data == b"\x34\x12"


def test_mr_reply_with_additional_status():
    reply = cip.encode_mr_reply(cip.FORWARD_OPEN, cip.CONNECTION_FAILURE,
                                additional=[0x0100])
    service, status, additional, data = cip.decode_mr_reply(reply)
    assert status == cip.CONNECTION_FAILURE
    assert additional == [0x0100]
    assert data == b""
