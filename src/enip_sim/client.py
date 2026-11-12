"""A minimal EtherNet/IP client, for trying the simulator out and for tests.

It registers a session, sends unconnected explicit messages (SendRRData) and reads the
List Identity response. For production use a full stack such as pycomm3; the test suite
cross-checks the simulator against pycomm3.
"""
from __future__ import annotations

import socket
import struct

from . import cip
from . import encapsulation as enc


class EnipError(Exception):
    """A CIP service answered a non-success general status."""

    def __init__(self, status: int):
        self.status = status
        name = cip.STATUS_NAMES.get(status, f"0x{status:02x}")
        super().__init__(f"CIP status 0x{status:02x} ({name})")


class EnipClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 44818,
                 *, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.session = 0
        self._sock: socket.socket | None = None

    # --- lifecycle -----------------------------------------------------------------
    def connect(self) -> "EnipClient":
        if self._sock is not None:
            return self
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        header, data = self._transact(enc.REGISTER_SESSION, bytes([1, 0, 0, 0]), session=0)
        self.session = header.session
        return self

    def close(self) -> None:
        if self._sock is None:
            return
        try:
            self._sock.sendall(enc.encode(enc.UNREGISTER_SESSION, session=self.session))
        except OSError:
            pass
        try:
            self._sock.close()
        finally:
            self._sock = None
            self.session = 0

    def __enter__(self) -> "EnipClient":
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.close()

    # --- transport -----------------------------------------------------------------
    def _recv_packet(self) -> bytes:
        head = self._recv_exact(enc.HEADER_LEN)
        length = int.from_bytes(head[2:4], "little")
        return head + (self._recv_exact(length) if length else b"")

    def _recv_exact(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = self._sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("connection closed by the server")
            data += chunk
        return data

    def _transact(self, command: int, data: bytes = b"",
                  session: int | None = None) -> tuple[enc.Header, bytes]:
        if self._sock is None:
            raise ConnectionError("not connected")
        handle = self.session if session is None else session
        self._sock.sendall(enc.encode(command, session=handle, data=data))
        return enc.decode(self._recv_packet())

    # --- explicit messaging --------------------------------------------------------
    def message(self, service: int, path: bytes, data: bytes = b"") -> tuple[int, bytes]:
        """Send one unconnected CIP request; return (general status, reply data)."""
        request = cip.encode_mr_request(service, path, data)
        header, body = self._transact(enc.SEND_RR_DATA, enc.encode_rr_data(request))
        if header.status != enc.STATUS_SUCCESS:
            raise ConnectionError(f"encapsulation status 0x{header.status:04x}")
        reply, _ = enc.decode_rr_data(body)
        _, status, _, reply_data = cip.decode_mr_reply(reply)
        return status, reply_data

    def _checked(self, service: int, path: bytes, data: bytes = b"") -> bytes:
        status, reply_data = self.message(service, path, data)
        if status != cip.SUCCESS:
            raise EnipError(status)
        return reply_data

    def get_attribute_single(self, class_id: int, instance: int, attribute: int) -> bytes:
        return self._checked(cip.GET_ATTRIBUTE_SINGLE,
                             cip.encode_path(class_id, instance, attribute))

    def set_attribute_single(self, class_id: int, instance: int, attribute: int,
                             data: bytes) -> None:
        self._checked(cip.SET_ATTRIBUTE_SINGLE,
                      cip.encode_path(class_id, instance, attribute), data)

    def get_attributes_all(self, class_id: int, instance: int) -> bytes:
        return self._checked(cip.GET_ATTRIBUTES_ALL, cip.encode_path(class_id, instance))

    def reset(self, class_id: int = cip.IDENTITY, instance: int = 1) -> None:
        self._checked(cip.RESET, cip.encode_path(class_id, instance))

    # --- discovery -----------------------------------------------------------------
    def list_identity(self) -> dict:
        header, body = self._transact(enc.LIST_IDENTITY)
        items = enc.decode_cpf(body)
        for type_id, data in items:
            if type_id == enc.ITEM_LIST_IDENTITY:
                return _parse_identity(data)
        raise ConnectionError("no List Identity item in the response")


def _parse_identity(data: bytes) -> dict:
    offset = 2 + 16                 # encapsulation version + socket address
    vendor_id, device_type, product_code = struct.unpack_from("<HHH", data, offset)
    offset += 6
    major, minor, status, serial = struct.unpack_from("<BBHI", data, offset)
    offset += 8
    name_length = data[offset]
    product_name = data[offset + 1:offset + 1 + name_length].decode("latin-1")
    offset += 1 + name_length
    state = data[offset] if offset < len(data) else None
    return {"vendor_id": vendor_id, "device_type": device_type,
            "product_code": product_code, "revision": {"major": major, "minor": minor},
            "status": status, "serial": serial, "product_name": product_name,
            "state": state}
