"""Common Packet Format and the SendRRData / SendUnitData bodies."""
from __future__ import annotations

import pytest

from enip_sim import encapsulation as enc


def test_cpf_round_trip():
    items = [(enc.ITEM_NULL_ADDRESS, b""),
             (enc.ITEM_UNCONNECTED_DATA, b"\x01\x02\x03")]
    buffer = enc.encode_cpf(items)
    # item count 2, then null-address (type 0, len 0), then data item (type 0x00B2, len 3)
    assert buffer == bytes.fromhex("0200" "0000 0000".replace(" ", "")
                                   + "b200" "0300" + "010203")
    assert enc.decode_cpf(buffer) == items


def test_cpf_rejects_bad_item_length():
    bad = bytes.fromhex("0100" "b200" "0500" "0102")  # claims 5 data bytes, only 2
    with pytest.raises(enc.EncapsulationError):
        enc.decode_cpf(bad)


def test_rr_data_round_trip():
    body = enc.encode_rr_data(b"\x0e\x03\x20\x01\x24\x01", timeout=5)
    cip, timeout = enc.decode_rr_data(body)
    assert cip == b"\x0e\x03\x20\x01\x24\x01"
    assert timeout == 5


def test_unit_data_carries_connection_and_sequence():
    body = enc.encode_unit_data(connection_id=0x0A0B0C0D, sequence=7, cip=b"\xaa\xbb")
    conn, seq, cip = enc.decode_unit_data(body)
    assert conn == 0x0A0B0C0D
    assert seq == 7
    assert cip == b"\xaa\xbb"
