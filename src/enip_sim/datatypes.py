"""CIP elementary data types (Volume 1, Appendix C).

Each type encodes to and decodes from little-endian bytes. ``SHORT_STRING`` is a
one-byte length followed by the characters; ``STRING`` a two-byte length. Integers
saturate nothing here -- an out-of-range value is an error, as a real device would
refuse it.
"""
from __future__ import annotations

import struct

# name -> (struct format, signed, bits) for the fixed-size elementary types
_INT = {
    "USINT": ("<B", False), "SINT": ("<b", True), "BYTE": ("<B", False),
    "UINT": ("<H", False), "INT": ("<h", True), "WORD": ("<H", False),
    "UDINT": ("<I", False), "DINT": ("<i", True), "DWORD": ("<I", False),
    "ULINT": ("<Q", False), "LINT": ("<q", True), "LWORD": ("<Q", False),
}
_FLOAT = {"REAL": "<f", "LREAL": "<d"}


class DataTypeError(ValueError):
    """An unknown CIP type or a value that does not fit it."""


def size_of(name: str) -> int | None:
    """Fixed size in bytes, or None for a variable-length type."""
    name = name.upper()
    if name == "BOOL":
        return 1
    if name in _INT:
        return struct.calcsize(_INT[name][0])
    if name in _FLOAT:
        return struct.calcsize(_FLOAT[name])
    if name in ("STRING", "SHORT_STRING"):
        return None
    raise DataTypeError(f"unknown CIP type {name!r}")


def encode(name: str, value) -> bytes:
    name = name.upper()
    if name == "BOOL":
        return b"\x01" if value else b"\x00"
    if name in _INT:
        fmt, signed = _INT[name]
        try:
            return struct.pack(fmt, int(value))
        except struct.error as error:
            raise DataTypeError(f"{value!r} does not fit a {name}: {error}") from None
    if name in _FLOAT:
        return struct.pack(_FLOAT[name], float(value))
    if name == "SHORT_STRING":
        raw = str(value).encode("latin-1")
        if len(raw) > 0xFF:
            raise DataTypeError("SHORT_STRING longer than 255 bytes")
        return bytes([len(raw)]) + raw
    if name == "STRING":
        raw = str(value).encode("latin-1")
        if len(raw) > 0xFFFF:
            raise DataTypeError("STRING longer than 65535 bytes")
        return struct.pack("<H", len(raw)) + raw
    raise DataTypeError(f"unknown CIP type {name!r}")


def decode(name: str, data: bytes):
    """Return (value, remaining bytes)."""
    name = name.upper()
    if name == "BOOL":
        if not data:
            raise DataTypeError("no data for a BOOL")
        return data[0] != 0, data[1:]
    if name in _INT:
        fmt, _ = _INT[name]
        size = struct.calcsize(fmt)
        if len(data) < size:
            raise DataTypeError(f"need {size} bytes for a {name}")
        return struct.unpack_from(fmt, data)[0], data[size:]
    if name in _FLOAT:
        fmt = _FLOAT[name]
        size = struct.calcsize(fmt)
        if len(data) < size:
            raise DataTypeError(f"need {size} bytes for a {name}")
        return struct.unpack_from(fmt, data)[0], data[size:]
    if name == "SHORT_STRING":
        if not data:
            raise DataTypeError("no data for a SHORT_STRING")
        length = data[0]
        if len(data) < 1 + length:
            raise DataTypeError("SHORT_STRING shorter than its length byte")
        return data[1:1 + length].decode("latin-1"), data[1 + length:]
    if name == "STRING":
        if len(data) < 2:
            raise DataTypeError("no data for a STRING")
        (length,) = struct.unpack_from("<H", data, 0)
        if len(data) < 2 + length:
            raise DataTypeError("STRING shorter than its length word")
        return data[2:2 + length].decode("latin-1"), data[2 + length:]
    raise DataTypeError(f"unknown CIP type {name!r}")
