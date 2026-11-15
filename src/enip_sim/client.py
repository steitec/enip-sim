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


class ForwardOpenError(EnipError):
    """A Forward_Open was rejected; ``extended`` is the extended status (e.g. 0x0109)."""

    def __init__(self, status: int, additional: list[int]):
        self.additional = list(additional)
        self.extended = additional[0] if additional else None
        super().__init__(status)
        if self.extended is not None:
            detail = _EXTENDED.get(self.extended, "")
            extra = f", expected size {additional[1]}" if self.extended == 0x0109 \
                and len(additional) > 1 else ""
            self.args = (f"{self.args[0]}, extended status 0x{self.extended:04x}"
                         f"{' (' + detail + ')' if detail else ''}{extra}",)


_EXTENDED = {
    0x0100: "connection in use / duplicate Forward_Open",
    0x0103: "transport class and trigger not supported",
    0x0106: "ownership conflict",
    0x0108: "invalid network connection parameter",
    0x0109: "invalid connection size",
    0x0111: "RPI not supported",
    0x0117: "invalid produced or consumed application path",
    0x0118: "invalid configuration application path",
}


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

    def request_items(self, service: int, path: bytes, data: bytes = b"",
                      items: list[tuple[int, bytes]] | tuple = ()
                      ) -> tuple[int, list[int], bytes, list[tuple[int, bytes]]]:
        """Like :meth:`message`, with extra CPF items both ways; returns
        (general status, additional status words, reply data, reply items)."""
        request = cip.encode_mr_request(service, path, data)
        header, body = self._transact(enc.SEND_RR_DATA,
                                      enc.encode_rr_data(request, extra_items=items))
        if header.status != enc.STATUS_SUCCESS:
            raise ConnectionError(f"encapsulation status 0x{header.status:04x}")
        reply, _, reply_items = enc.decode_rr_items(body)
        _, status, additional, reply_data = cip.decode_mr_reply(reply)
        return status, additional, reply_data, reply_items

    # --- implicit messaging --------------------------------------------------------
    def open_io(self, *, output_instance: int, input_instance: int, config_instance: int,
                output_size: int, input_size: int, rpi_ms: float = 50,
                timeout_multiplier: int = 2, input_only: bool = False,
                heartbeat_instance: int = 198, large: bool = False):
        """Open a Class 1 (cyclic, point-to-point) connection and return an
        :class:`~enip_sim.originator.IOConnection`. Raises ForwardOpenError when the
        device rejects it. Use it as a context manager, or call ``close()``."""
        from .originator import open_io
        return open_io(self, output_instance=output_instance, input_instance=input_instance,
                       config_instance=config_instance, output_size=output_size,
                       input_size=input_size, rpi_ms=rpi_ms,
                       timeout_multiplier=timeout_multiplier, input_only=input_only,
                       heartbeat_instance=heartbeat_instance, large=large)

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
