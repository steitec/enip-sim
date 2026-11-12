"""CIP (Common Industrial Protocol): EPATH logical segments and Message Router framing.

A CIP message is a service code, a request path (EPATH) that names an object, and
service data. The reply echoes the service with the high bit set, a general status
and any additional status. References: ODVA "The CIP Networks Library, Volume 1:
Common Industrial Protocol", chapters 2 (object model), Appendix B (status codes)
and Appendix C (EPATH).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

# --- CIP service codes (Volume 1, Appendix A) ---------------------------------------
GET_ATTRIBUTES_ALL = 0x01
SET_ATTRIBUTES_ALL = 0x02
GET_ATTRIBUTE_LIST = 0x03
SET_ATTRIBUTE_LIST = 0x04
RESET = 0x05
START = 0x06
STOP = 0x07
CREATE = 0x08
DELETE = 0x09
GET_ATTRIBUTE_SINGLE = 0x0E
SET_ATTRIBUTE_SINGLE = 0x10
# Connection Manager services
FORWARD_CLOSE = 0x4E
UNCONNECTED_SEND = 0x52
FORWARD_OPEN = 0x54
GET_CONNECTION_DATA = 0x56
LARGE_FORWARD_OPEN = 0x5B

SERVICE_NAMES = {
    GET_ATTRIBUTES_ALL: "Get_Attributes_All", SET_ATTRIBUTES_ALL: "Set_Attributes_All",
    GET_ATTRIBUTE_LIST: "Get_Attribute_List", SET_ATTRIBUTE_LIST: "Set_Attribute_List",
    RESET: "Reset", START: "Start", STOP: "Stop", CREATE: "Create", DELETE: "Delete",
    GET_ATTRIBUTE_SINGLE: "Get_Attribute_Single", SET_ATTRIBUTE_SINGLE: "Set_Attribute_Single",
    FORWARD_CLOSE: "Forward_Close", UNCONNECTED_SEND: "Unconnected_Send",
    FORWARD_OPEN: "Forward_Open", LARGE_FORWARD_OPEN: "Large_Forward_Open",
}

# --- CIP general status codes (Volume 1, Appendix B) --------------------------------
SUCCESS = 0x00
CONNECTION_FAILURE = 0x01
RESOURCE_UNAVAILABLE = 0x02
INVALID_PARAMETER_VALUE = 0x03
PATH_SEGMENT_ERROR = 0x04
PATH_DESTINATION_UNKNOWN = 0x05
SERVICE_NOT_SUPPORTED = 0x08
INVALID_ATTRIBUTE_VALUE = 0x09
ALREADY_IN_MODE = 0x0B
OBJECT_STATE_CONFLICT = 0x0C
ATTRIBUTE_NOT_SETTABLE = 0x0E
PRIVILEGE_VIOLATION = 0x0F
DEVICE_STATE_CONFLICT = 0x10
NOT_ENOUGH_DATA = 0x13
ATTRIBUTE_NOT_SUPPORTED = 0x14
TOO_MUCH_DATA = 0x15
OBJECT_DOES_NOT_EXIST = 0x16
INVALID_PARAMETER = 0x20
INVALID_PATH_SIZE = 0x26

STATUS_NAMES = {
    SUCCESS: "Success", CONNECTION_FAILURE: "Connection failure",
    RESOURCE_UNAVAILABLE: "Resource unavailable",
    INVALID_PARAMETER_VALUE: "Invalid parameter value",
    PATH_SEGMENT_ERROR: "Path segment error",
    PATH_DESTINATION_UNKNOWN: "Path destination unknown",
    SERVICE_NOT_SUPPORTED: "Service not supported",
    INVALID_ATTRIBUTE_VALUE: "Invalid attribute value",
    ALREADY_IN_MODE: "Already in requested mode or state",
    OBJECT_STATE_CONFLICT: "Object state conflict",
    ATTRIBUTE_NOT_SETTABLE: "Attribute not settable",
    PRIVILEGE_VIOLATION: "Privilege violation",
    DEVICE_STATE_CONFLICT: "Device state conflict",
    NOT_ENOUGH_DATA: "Not enough data", ATTRIBUTE_NOT_SUPPORTED: "Attribute not supported",
    TOO_MUCH_DATA: "Too much data", OBJECT_DOES_NOT_EXIST: "Object does not exist",
    INVALID_PARAMETER: "Invalid parameter", INVALID_PATH_SIZE: "Invalid path size",
}

# --- Standard CIP class ids ---------------------------------------------------------
IDENTITY = 0x01
MESSAGE_ROUTER = 0x02
ASSEMBLY = 0x04
CONNECTION_MANAGER = 0x06
TCPIP_INTERFACE = 0xF5
ETHERNET_LINK = 0xF6

REPLY_BIT = 0x80


class CIPError(ValueError):
    """A malformed CIP message or EPATH."""


# --- EPATH (logical segments only) --------------------------------------------------
# Logical segment type byte: 0b001_TTT_FF, TTT = class 0 / instance 1 / attribute 4 /
# member 5, FF = size 0 (8-bit) / 1 (16-bit). 16-bit values are preceded by a pad byte.
_CLASS_8, _CLASS_16 = 0x20, 0x21
_INSTANCE_8, _INSTANCE_16 = 0x24, 0x25
_ATTRIBUTE_8, _ATTRIBUTE_16 = 0x30, 0x31
_MEMBER_8, _MEMBER_16 = 0x28, 0x29


@dataclass
class Path:
    class_id: int | None = None
    instance: int | None = None
    attribute: int | None = None
    member: int | None = None


def _segment(value: int, tag8: int, tag16: int) -> bytes:
    if value < 0x100:
        return bytes([tag8, value])
    return bytes([tag16, 0x00]) + struct.pack("<H", value)


def encode_path(class_id: int, instance: int | None = None,
                attribute: int | None = None, member: int | None = None) -> bytes:
    """Build an EPATH of logical class/instance/attribute/member segments."""
    out = _segment(class_id, _CLASS_8, _CLASS_16)
    if instance is not None:
        out += _segment(instance, _INSTANCE_8, _INSTANCE_16)
    if attribute is not None:
        out += _segment(attribute, _ATTRIBUTE_8, _ATTRIBUTE_16)
    if member is not None:
        out += _segment(member, _MEMBER_8, _MEMBER_16)
    return out


def decode_path(epath: bytes) -> Path:
    """Parse logical class/instance/attribute/member segments into a :class:`Path`."""
    path = Path()
    i = 0
    setters = {
        _CLASS_8: ("class_id", 1), _CLASS_16: ("class_id", 2),
        _INSTANCE_8: ("instance", 1), _INSTANCE_16: ("instance", 2),
        _ATTRIBUTE_8: ("attribute", 1), _ATTRIBUTE_16: ("attribute", 2),
        _MEMBER_8: ("member", 1), _MEMBER_16: ("member", 2),
    }
    while i < len(epath):
        tag = epath[i]
        if tag not in setters:
            raise CIPError(f"unsupported path segment 0x{tag:02x}")
        name, width = setters[tag]
        i += 1
        if width == 2:
            i += 1  # pad byte
            if i + 2 > len(epath):
                raise CIPError("16-bit path segment runs past the EPATH")
            (value,) = struct.unpack_from("<H", epath, i)
            i += 2
        else:
            if i >= len(epath):
                raise CIPError("8-bit path segment runs past the EPATH")
            value = epath[i]
            i += 1
        setattr(path, name, value)
    return path


# --- Message Router request / reply -------------------------------------------------

def encode_mr_request(service: int, path: bytes, data: bytes = b"") -> bytes:
    """service, path size in 16-bit words, path, then service data."""
    if len(path) % 2:
        raise CIPError("EPATH length must be a whole number of 16-bit words")
    return bytes([service, len(path) // 2]) + path + data


def decode_mr_request(request: bytes) -> tuple[int, bytes, bytes]:
    """Return (service, path bytes, service data)."""
    if len(request) < 2:
        raise CIPError("Message Router request too short")
    service = request[0]
    words = request[1]
    end = 2 + 2 * words
    if end > len(request):
        raise CIPError("request path size runs past the message")
    return service, request[2:end], request[end:]


def encode_mr_reply(service: int, status: int, *, additional: list[int] | None = None,
                    data: bytes = b"") -> bytes:
    """reply service (high bit set), reserved, status, additional status, data."""
    additional = additional or []
    head = bytes([(service | REPLY_BIT) & 0xFF, 0x00, status, len(additional)])
    extra = b"".join(struct.pack("<H", word) for word in additional)
    return head + extra + data


def decode_mr_reply(reply: bytes) -> tuple[int, int, list[int], bytes]:
    """Return (service without reply bit, status, additional status words, data)."""
    if len(reply) < 4:
        raise CIPError("Message Router reply too short")
    service = reply[0] & ~REPLY_BIT
    status = reply[2]
    count = reply[3]
    end = 4 + 2 * count
    if end > len(reply):
        raise CIPError("additional status size runs past the reply")
    additional = list(struct.unpack_from(f"<{count}H", reply, 4)) if count else []
    return service, status, additional, reply[end:]
