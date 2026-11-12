"""EtherNet/IP encapsulation: the 24-byte header and the Common Packet Format (CPF).

EtherNet/IP wraps CIP messages in an encapsulation header over TCP (and UDP) port
44818. All integers are little-endian. References: ODVA "The CIP Networks Library,
Volume 2: EtherNet/IP Adaptation of CIP", chapter 2 (encapsulation) and 3-2 (CPF).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

HEADER_LEN = 24

# Encapsulation commands (Volume 2, table 2-3.3)
NOP = 0x0000
LIST_SERVICES = 0x0004
LIST_IDENTITY = 0x0063
LIST_INTERFACES = 0x0064
REGISTER_SESSION = 0x0065
UNREGISTER_SESSION = 0x0066
SEND_RR_DATA = 0x006F
SEND_UNIT_DATA = 0x0070

COMMAND_NAMES = {
    NOP: "NOP", LIST_SERVICES: "ListServices", LIST_IDENTITY: "ListIdentity",
    LIST_INTERFACES: "ListInterfaces", REGISTER_SESSION: "RegisterSession",
    UNREGISTER_SESSION: "UnRegisterSession", SEND_RR_DATA: "SendRRData",
    SEND_UNIT_DATA: "SendUnitData",
}

# Encapsulation status codes (Volume 2, table 2-3.3)
STATUS_SUCCESS = 0x0000
STATUS_INVALID_COMMAND = 0x0001
STATUS_INSUFFICIENT_MEMORY = 0x0002
STATUS_INCORRECT_DATA = 0x0003
STATUS_INVALID_SESSION = 0x0064
STATUS_INVALID_LENGTH = 0x0065
STATUS_UNSUPPORTED_VERSION = 0x0069

# Common Packet Format item type ids (Volume 2, table 2-6.6)
ITEM_NULL_ADDRESS = 0x0000
ITEM_CONNECTED_ADDRESS = 0x00A1
ITEM_CONNECTED_DATA = 0x00B1
ITEM_UNCONNECTED_DATA = 0x00B2
ITEM_LIST_IDENTITY = 0x000C
ITEM_LIST_SERVICE = 0x0100
ITEM_SOCKADDR_O2T = 0x8000
ITEM_SOCKADDR_T2O = 0x8001
ITEM_SEQUENCED_ADDRESS = 0x8002

_HEADER = struct.Struct("<HHII8sI")


class EncapsulationError(ValueError):
    """A malformed encapsulation packet."""


@dataclass
class Header:
    command: int
    length: int = 0
    session: int = 0
    status: int = 0
    context: bytes = b"\x00" * 8
    options: int = 0


def encode(command: int, *, session: int = 0, status: int = 0,
           context: bytes = b"\x00" * 8, options: int = 0, data: bytes = b"") -> bytes:
    """Build a complete encapsulation packet (header + data)."""
    context = (bytes(context) + b"\x00" * 8)[:8]
    return _HEADER.pack(command, len(data), session, status, context, options) + data


def decode(packet: bytes) -> tuple[Header, bytes]:
    """Split a complete packet into its header and the data that follows it."""
    if len(packet) < HEADER_LEN:
        raise EncapsulationError(f"packet shorter than the {HEADER_LEN}-byte header")
    command, length, session, status, context, options = _HEADER.unpack(packet[:HEADER_LEN])
    data = packet[HEADER_LEN:]
    if len(data) != length:
        raise EncapsulationError(
            f"length field is {length} but {len(data)} data bytes follow the header")
    return Header(command, length, session, status, context, options), data


# --- Common Packet Format -----------------------------------------------------------

def encode_cpf(items: list[tuple[int, bytes]]) -> bytes:
    """Pack an item count followed by (type id, length, data) for each item."""
    out = struct.pack("<H", len(items))
    for type_id, data in items:
        out += struct.pack("<HH", type_id, len(data)) + data
    return out


def decode_cpf(buffer: bytes) -> list[tuple[int, bytes]]:
    """Unpack the item list; raise on a length that runs past the buffer."""
    if len(buffer) < 2:
        raise EncapsulationError("CPF buffer too short for an item count")
    (count,) = struct.unpack_from("<H", buffer, 0)
    items: list[tuple[int, bytes]] = []
    offset = 2
    for _ in range(count):
        if offset + 4 > len(buffer):
            raise EncapsulationError("CPF item header runs past the buffer")
        type_id, length = struct.unpack_from("<HH", buffer, offset)
        offset += 4
        if offset + length > len(buffer):
            raise EncapsulationError("CPF item data runs past the buffer")
        items.append((type_id, buffer[offset:offset + length]))
        offset += length
    return items


# --- SendRRData / SendUnitData bodies -----------------------------------------------
# Both start with an interface handle (UDINT, 0 for CIP) and a timeout (UINT), then CPF.

def encode_rr_data(cip: bytes, *, timeout: int = 0, interface: int = 0) -> bytes:
    """Unconnected message: null address item + unconnected data item."""
    cpf = encode_cpf([(ITEM_NULL_ADDRESS, b""), (ITEM_UNCONNECTED_DATA, cip)])
    return struct.pack("<IH", interface, timeout) + cpf


def decode_rr_data(body: bytes) -> tuple[bytes, int]:
    """Return (cip message, timeout) from a SendRRData body."""
    if len(body) < 6:
        raise EncapsulationError("SendRRData body too short")
    interface, timeout = struct.unpack_from("<IH", body, 0)
    items = decode_cpf(body[6:])
    for type_id, data in items:
        if type_id == ITEM_UNCONNECTED_DATA:
            return data, timeout
    raise EncapsulationError("SendRRData without an unconnected data item")


def encode_unit_data(*, connection_id: int, sequence: int, cip: bytes,
                     interface: int = 0, timeout: int = 0) -> bytes:
    """Connected message: connected address item + connected data item (seq + CIP)."""
    address = struct.pack("<I", connection_id)
    data = struct.pack("<H", sequence) + cip
    cpf = encode_cpf([(ITEM_CONNECTED_ADDRESS, address), (ITEM_CONNECTED_DATA, data)])
    return struct.pack("<IH", interface, timeout) + cpf


def decode_unit_data(body: bytes) -> tuple[int, int, bytes]:
    """Return (connection id, sequence count, cip message) from a SendUnitData body."""
    if len(body) < 6:
        raise EncapsulationError("SendUnitData body too short")
    items = decode_cpf(body[6:])
    connection_id = None
    payload = None
    for type_id, data in items:
        if type_id == ITEM_CONNECTED_ADDRESS and len(data) >= 4:
            (connection_id,) = struct.unpack_from("<I", data, 0)
        elif type_id == ITEM_CONNECTED_DATA:
            payload = data
    if connection_id is None or payload is None or len(payload) < 2:
        raise EncapsulationError("SendUnitData without a connected address and data item")
    (sequence,) = struct.unpack_from("<H", payload, 0)
    return connection_id, sequence, payload[2:]
