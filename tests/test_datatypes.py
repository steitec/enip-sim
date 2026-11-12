"""CIP elementary data types: encode and decode attribute values."""
from __future__ import annotations

import pytest

from enip_sim import datatypes as dt


@pytest.mark.parametrize("name, value, raw", [
    ("USINT", 7, "07"),
    ("UINT", 0x1234, "3412"),
    ("UDINT", 0x01020304, "04030201"),
    ("INT", -2, "feff"),
    ("DINT", -1, "ffffffff"),
    ("WORD", 0xABCD, "cdab"),
    ("BOOL", True, "01"),
    ("BOOL", False, "00"),
])
def test_encode_decode_round_trip(name, value, raw):
    assert dt.encode(name, value).hex() == raw
    decoded, rest = dt.decode(name, bytes.fromhex(raw))
    assert decoded == value
    assert rest == b""


def test_real_round_trip():
    raw = dt.encode("REAL", 1.5)
    assert raw == bytes.fromhex("0000c03f")   # IEEE-754 little-endian
    value, rest = dt.decode("REAL", raw)
    assert value == pytest.approx(1.5)
    assert rest == b""


def test_short_string():
    raw = dt.encode("SHORT_STRING", "Drive")
    assert raw == bytes([5]) + b"Drive"
    value, rest = dt.decode("SHORT_STRING", raw + b"\xff")
    assert value == "Drive"
    assert rest == b"\xff"


def test_unsigned_overflow_is_rejected():
    with pytest.raises(dt.DataTypeError):
        dt.encode("USINT", 256)


def test_unknown_type_is_rejected():
    with pytest.raises(dt.DataTypeError):
        dt.encode("FROB", 1)


def test_size_of():
    assert dt.size_of("UDINT") == 4
    assert dt.size_of("BOOL") == 1
    assert dt.size_of("SHORT_STRING") is None   # variable length
