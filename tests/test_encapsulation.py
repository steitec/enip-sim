"""EtherNet/IP encapsulation layer: the 24-byte header and the Common Packet Format."""
from __future__ import annotations

import pytest

from enip_sim import encapsulation as enc


def test_register_session_request_bytes():
    # A RegisterSession request as a real scanner sends it: command 0x0065, a 4-byte
    # body (protocol version 1, options 0), everything else zero. Little-endian.
    packet = enc.encode(enc.REGISTER_SESSION, data=bytes([1, 0, 0, 0]))
    assert packet == bytes.fromhex(
        "6500"            # command 0x0065
        "0400"            # length 4
        "00000000"        # session handle
        "00000000"        # status
        "0000000000000000"  # sender context
        "00000000"        # options
        "01000000"        # data: protocol version 1, options 0
    )


def test_decode_round_trip():
    original = enc.encode(enc.SEND_RR_DATA, session=0x12345678,
                          context=b"ABCDEFGH", data=b"\xde\xad\xbe\xef")
    header, data = enc.decode(original)
    assert header.command == enc.SEND_RR_DATA
    assert header.session == 0x12345678
    assert header.status == 0
    assert header.context == b"ABCDEFGH"
    assert header.length == 4
    assert data == b"\xde\xad\xbe\xef"


def test_decode_rejects_short_buffer():
    with pytest.raises(enc.EncapsulationError):
        enc.decode(b"\x65\x00\x04")


def test_decode_rejects_truncated_data():
    # header says 8 data bytes, only 2 present
    header_only = enc.encode(enc.SEND_RR_DATA, data=b"\x00" * 8)[:24]
    with pytest.raises(enc.EncapsulationError):
        enc.decode(header_only + b"\x01\x02")
