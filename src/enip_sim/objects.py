"""CIP objects: an attribute dictionary plus the standard attribute services.

An object answers Get_Attribute_Single, Get_Attributes_All and Set_Attribute_Single;
anything else (Reset, Forward_Open, model-specific services) is supplied as an extra
service handler. Builders create the standard objects a device needs.
"""
from __future__ import annotations

from typing import Callable

from . import cip, datatypes

ServiceHandler = Callable[[cip.Path, bytes], "tuple[int, bytes, list[int]]"]


class Attribute:
    """One CIP attribute. ``RAW`` keeps the value as bytes (fixed ``size`` optional);
    any other type name is a CIP elementary type encoded through :mod:`datatypes`.
    A ``getter``/``setter`` binds the attribute to a live value such as a model."""

    def __init__(self, type_name: str, value=None, *, settable: bool = False,
                 size: int | None = None, getter: Callable | None = None,
                 setter: Callable | None = None):
        self.type_name = type_name.upper()
        self._value = value
        self.settable = settable
        self.size = size
        self.getter = getter
        self.setter = setter

    @property
    def value(self):
        return self.getter() if self.getter is not None else self._value

    def _store(self, value) -> None:
        if self.setter is not None:
            self.setter(value)
        else:
            self._value = value

    def get_bytes(self) -> bytes:
        if self.type_name == "RAW":
            raw = bytes(self.value)
            if self.size is not None:
                raw = (raw + b"\x00" * self.size)[:self.size]
            return raw
        return datatypes.encode(self.type_name, self.value)

    def set_bytes(self, data: bytes) -> int:
        if self.type_name == "RAW":
            if self.size is not None:
                if len(data) < self.size:
                    return cip.NOT_ENOUGH_DATA
                if len(data) > self.size:
                    return cip.TOO_MUCH_DATA
            self._store(bytes(data))
            return cip.SUCCESS
        try:
            value, rest = datatypes.decode(self.type_name, data)
        except datatypes.DataTypeError:
            return cip.NOT_ENOUGH_DATA
        if rest:
            return cip.TOO_MUCH_DATA
        self._store(value)
        return cip.SUCCESS


class CIPObject:
    def __init__(self, class_id: int, instance: int, attributes: dict[int, Attribute],
                 *, all_order: list[int] | None = None,
                 services: dict[int, ServiceHandler] | None = None):
        self.class_id = class_id
        self.instance = instance
        self.attributes = attributes
        self.all_order = all_order if all_order is not None else sorted(attributes)
        self.extra_services = services or {}

    def service(self, code: int, path: cip.Path, data: bytes) -> tuple[int, bytes, list[int]]:
        if code in self.extra_services:
            return self.extra_services[code](path, data)
        if code == cip.GET_ATTRIBUTE_SINGLE:
            attribute = self.attributes.get(path.attribute)
            if attribute is None:
                return cip.ATTRIBUTE_NOT_SUPPORTED, b"", []
            return cip.SUCCESS, attribute.get_bytes(), []
        if code == cip.GET_ATTRIBUTES_ALL:
            out = b"".join(self.attributes[a].get_bytes()
                           for a in self.all_order if a in self.attributes)
            return cip.SUCCESS, out, []
        if code == cip.SET_ATTRIBUTE_SINGLE:
            attribute = self.attributes.get(path.attribute)
            if attribute is None:
                return cip.ATTRIBUTE_NOT_SUPPORTED, b"", []
            if not attribute.settable:
                return cip.ATTRIBUTE_NOT_SETTABLE, b"", []
            return attribute.set_bytes(data), b"", []
        return cip.SERVICE_NOT_SUPPORTED, b"", []


# --- builders for the standard objects ----------------------------------------------

def identity_object(*, vendor_id: int, device_type: int, product_code: int,
                    major: int, minor: int, status: int, serial: int,
                    product_name: str) -> CIPObject:
    """Identity object (class 0x01), the seven required attributes in order."""
    attributes = {
        1: Attribute("UINT", vendor_id),
        2: Attribute("UINT", device_type),
        3: Attribute("UINT", product_code),
        4: Attribute("RAW", bytes([major & 0xFF, minor & 0xFF]), size=2),
        5: Attribute("UINT", status),
        6: Attribute("UDINT", serial),
        7: Attribute("SHORT_STRING", product_name),
    }
    return CIPObject(cip.IDENTITY, 1, attributes, all_order=[1, 2, 3, 4, 5, 6, 7])


def assembly_object(*, instance: int, size: int, data: bytes | None = None,
                    settable: bool = True, getter: Callable | None = None,
                    setter: Callable | None = None) -> CIPObject:
    """Assembly object (class 0x04): attribute 3 is the data, attribute 4 its size."""
    buffer = bytes(data) if data is not None else b"\x00" * size
    attributes = {
        3: Attribute("RAW", buffer, settable=settable, size=size,
                     getter=getter, setter=setter),
        4: Attribute("UINT", size),
    }
    return CIPObject(cip.ASSEMBLY, instance, attributes, all_order=[3])


def message_router_object(classes: list[int]) -> CIPObject:
    """Message Router object (class 0x02). Attribute 1 lists the implemented classes."""
    import struct
    body = struct.pack("<H", len(classes)) + b"".join(
        struct.pack("<H", c) for c in classes)
    return CIPObject(cip.MESSAGE_ROUTER, 1, {1: Attribute("RAW", body)}, all_order=[1])


def connection_manager_object() -> CIPObject:
    """Connection Manager (class 0x06). Forward_Open/Close are served by the server;
    the object exists so the request path resolves and its counters can be read."""
    attributes = {
        1: Attribute("UINT", 0),   # Open Requests
        2: Attribute("UINT", 0),   # Open Format Rejects
        3: Attribute("UINT", 0),   # Open Resource Rejects
        8: Attribute("UINT", 0),   # Connection Timeouts
    }
    return CIPObject(cip.CONNECTION_MANAGER, 1, attributes, all_order=[1, 2, 3, 8])


def tcpip_interface_object(*, ip: str = "0.0.0.0", netmask: str = "0.0.0.0",
                           gateway: str = "0.0.0.0", host_name: str = "",
                           domain: str = "") -> CIPObject:
    """TCP/IP Interface object (class 0xF5), the attributes a scanner reads to learn
    the network configuration."""
    import socket as _socket
    import struct

    def packed_ip(address: str) -> int:
        return struct.unpack("<I", _socket.inet_aton(address))[0]

    physical_link = struct.pack("<H", 2) + cip.encode_path(cip.ETHERNET_LINK, 1)
    interface_config = (struct.pack("<IIIII", packed_ip(ip), packed_ip(netmask),
                                    packed_ip(gateway), 0, 0)
                        + struct.pack("<H", len(domain)) + domain.encode("latin-1"))
    attributes = {
        1: Attribute("DWORD", 0x1),                 # Interface Status: configured
        2: Attribute("DWORD", 0x4),                 # Configuration Capability
        3: Attribute("DWORD", 0x0),                 # Configuration Control: static
        4: Attribute("RAW", physical_link),         # Physical Link Object
        5: Attribute("RAW", interface_config),      # Interface Configuration
        6: Attribute("STRING", host_name),          # Host Name
    }
    return CIPObject(cip.TCPIP_INTERFACE, 1, attributes, all_order=[1, 2, 3, 4, 5, 6])


def ethernet_link_object(*, speed: int = 100, mac: bytes = b"\x00" * 6) -> CIPObject:
    """Ethernet Link object (class 0xF6): interface speed, flags and MAC address."""
    attributes = {
        1: Attribute("UDINT", speed),                       # Interface Speed (Mbps)
        2: Attribute("DWORD", 0xF),                         # Flags: link up, full duplex
        3: Attribute("RAW", (bytes(mac) + b"\x00" * 6)[:6]),  # Physical Address
    }
    return CIPObject(cip.ETHERNET_LINK, 1, attributes, all_order=[1, 2, 3])
