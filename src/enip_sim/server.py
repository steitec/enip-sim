"""The EtherNet/IP server: TCP sessions, the encapsulation commands and explicit messaging.

One server hosts one :class:`~enip_sim.device.Device` on EtherNet/IP TCP port 44818.
It registers sessions, answers the List* queries, passes CIP messages (direct or wrapped
in an Unconnected_Send) to the device, and applies the transport faults. A background
ticker advances the device's behaviour model while the server runs.
"""
from __future__ import annotations

import os
import socket
import socketserver
import struct
import threading
import time
from dataclasses import dataclass
from itertools import count

from . import cip
from . import encapsulation as enc
from .connection import ConnectionManager
from .device import Device

TCP_PORT = 44818
_TICK = 0.05  # s; how often the behaviour model advances


@dataclass
class Reply:
    data: bytes | None          # None: send nothing
    close: bool = False         # close the TCP connection after (not) sending
    delay: float = 0.0          # wait this long before sending


class Server:
    def __init__(self, device: Device, host: str = "127.0.0.1", port: int = TCP_PORT):
        self.device = device
        self.host = host
        self.port = port
        self.sessions: set[int] = set()
        self.connections = ConnectionManager()
        self._handles = count(1)
        self._server: _TcpServer | None = None
        self._thread: threading.Thread | None = None
        self._ticker: threading.Thread | None = None
        self._stop = threading.Event()

    # --- lifecycle -----------------------------------------------------------------
    def start(self) -> int:
        self._stop.clear()
        self._server = _TcpServer((self.host, self.port), self)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        kwargs={"poll_interval": 0.05}, daemon=True,
                                        name="enip-sim-tcp")
        self._thread.start()
        if self.device.model is not None:
            self._ticker = threading.Thread(target=self._run_ticker, daemon=True,
                                            name="enip-sim-tick")
            self._ticker.start()
        return self.port

    def _run_ticker(self) -> None:
        # Advance by the real elapsed time, not a fixed step, so the simulated time
        # tracks the wall clock even when the thread is scheduled late (slow or loaded
        # machines, CI). A long stall is clamped so it does not jump the model forward.
        last = time.monotonic()
        while not self._stop.wait(_TICK):
            now = time.monotonic()
            self.device.tick(min(now - last, 1.0))
            last = now

    def stop(self) -> None:
        self._stop.set()
        if self._ticker is not None:
            self._ticker.join(timeout=5)
            self._ticker = None
        if self._server is None:
            return
        self._server.stopping.set()
        self._server.shutdown()
        self._server.server_close()
        for connection in list(self._server.connections):
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self._thread.join(timeout=5)
        self._server = self._thread = None

    def describe(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"ethernet/ip {host}:{self.port}"

    # --- encapsulation command dispatch --------------------------------------------
    def connecting(self) -> bool:
        """A new TCP connection: honour the 'refuse' fault by closing at once."""
        return self.device.faults.take("refuse") is None

    def handle_packet(self, header: enc.Header, data: bytes) -> Reply:
        command = header.command
        if command == enc.NOP:
            return Reply(None)
        if command == enc.REGISTER_SESSION:
            handle = next(self._handles)
            self.sessions.add(handle)
            body = data[:4] or b"\x01\x00\x00\x00"
            return Reply(self._packet(header, session=handle, data=body))
        if command == enc.UNREGISTER_SESSION:
            self.sessions.discard(header.session)
            return Reply(None)
        if command == enc.LIST_IDENTITY:
            item = enc.encode_cpf([(enc.ITEM_LIST_IDENTITY, self._identity_item())])
            return Reply(self._packet(header, data=item))
        if command == enc.LIST_SERVICES:
            item = enc.encode_cpf([(enc.ITEM_LIST_SERVICE, self._service_item())])
            return Reply(self._packet(header, data=item))
        if command == enc.LIST_INTERFACES:
            return Reply(self._packet(header, data=enc.encode_cpf([])))
        if command in (enc.SEND_RR_DATA, enc.SEND_UNIT_DATA):
            if header.session not in self.sessions:
                return Reply(self._packet(header, status=enc.STATUS_INVALID_SESSION))
            return self._messaging(header, data)
        return Reply(self._packet(header, status=enc.STATUS_INVALID_COMMAND))

    def _packet(self, header: enc.Header, *, session: int | None = None,
                status: int = enc.STATUS_SUCCESS, data: bytes = b"") -> bytes:
        return enc.encode(header.command,
                          session=header.session if session is None else session,
                          status=status, context=header.context, data=data)

    def _messaging(self, header: enc.Header, data: bytes) -> Reply:
        faults = self.device.faults
        if faults.take("drop_connection") is not None:
            return Reply(None, close=True)
        if faults.take("no_response") is not None:
            return Reply(None)
        delay = faults.take("delay")
        seconds = delay["seconds"] if delay else 0.0

        if header.command == enc.SEND_RR_DATA:
            try:
                request, timeout = enc.decode_rr_data(data)
            except enc.EncapsulationError:
                return Reply(self._packet(header, status=enc.STATUS_INCORRECT_DATA))
            reply = self.process_cip(request)
            body = enc.encode_rr_data(reply, timeout=timeout)
            return Reply(self._packet(header, data=body), delay=seconds)
        try:
            connection_id, sequence, request = enc.decode_unit_data(data)
        except enc.EncapsulationError:
            return Reply(self._packet(header, status=enc.STATUS_INCORRECT_DATA))
        reply = self.process_cip(request)
        connection = self.connections.lookup(connection_id)
        reply_cid = connection.to_cid if connection is not None else connection_id
        body = enc.encode_unit_data(connection_id=reply_cid, sequence=sequence, cip=reply)
        return Reply(self._packet(header, data=body), delay=seconds)

    def process_cip(self, request: bytes) -> bytes:
        """Answer a CIP request: connection services here, everything else at the device."""
        try:
            service, path_bytes, cdata = cip.decode_mr_request(request)
            path = cip.decode_path(path_bytes)
        except cip.CIPError:
            return cip.encode_mr_reply(0x00, cip.PATH_SEGMENT_ERROR)
        if path.class_id == cip.CONNECTION_MANAGER:
            if service in (cip.FORWARD_OPEN, cip.LARGE_FORWARD_OPEN):
                reject = self.device.faults.take("forward_open_reject")
                status, rdata, add = self.connections.forward_open(
                    cdata, reject_status=reject["status"] if reject else None)
                return cip.encode_mr_reply(service, status, additional=add, data=rdata)
            if service == cip.FORWARD_CLOSE:
                status, rdata, add = self.connections.forward_close(cdata)
                return cip.encode_mr_reply(service, status, additional=add, data=rdata)
            if service == cip.UNCONNECTED_SEND:
                return self._unconnected_send(request)
        return self.device.handle(request)

    def _unconnected_send(self, request: bytes) -> bytes:
        _, _, data = cip.decode_mr_request(request)
        if len(data) < 4:
            return cip.encode_mr_reply(cip.UNCONNECTED_SEND, cip.NOT_ENOUGH_DATA)
        size = struct.unpack_from("<H", data, 2)[0]
        embedded = data[4:4 + size]
        return self.device.handle(embedded)

    # --- List* item bodies ---------------------------------------------------------
    def _identity_item(self) -> bytes:
        identity = self.device.find(cip.IDENTITY, 1)
        attrs = identity.attributes if identity is not None else {}

        def raw(n: int, default: bytes) -> bytes:
            return attrs[n].get_bytes() if n in attrs else default

        sockaddr = struct.pack(">hHI8s", socket.AF_INET, self.port or TCP_PORT, 0, b"\x00" * 8)
        return (struct.pack("<H", 1)              # encapsulation protocol version
                + sockaddr
                + raw(1, b"\x00\x00")             # vendor id
                + raw(2, b"\x00\x00")             # device type
                + raw(3, b"\x00\x00")             # product code
                + raw(4, b"\x01\x00")             # revision major, minor
                + raw(5, b"\x00\x00")             # status
                + raw(6, b"\x00\x00\x00\x00")     # serial number
                + raw(7, b"\x00")                 # product name (SHORT_STRING)
                + b"\x03")                        # state: operational

    def _service_item(self) -> bytes:
        name = b"Communications\x00\x00"
        return struct.pack("<HH", 1, 0x0020) + name


# --- socketserver plumbing ----------------------------------------------------------

class _TcpHandler(socketserver.BaseRequestHandler):
    def setup(self) -> None:
        self.server.connections.add(self.request)

    def finish(self) -> None:
        self.server.connections.discard(self.request)

    def _recv_exact(self, n: int) -> bytes | None:
        data = b""
        while len(data) < n:
            try:
                chunk = self.request.recv(n - len(data))
            except OSError:
                return None
            if not chunk:
                return None
            data += chunk
        return data

    def handle(self) -> None:
        enip = self.server.enip
        if not enip.connecting():
            return
        while not self.server.stopping.is_set():
            head = self._recv_exact(enc.HEADER_LEN)
            if head is None:
                return
            length = int.from_bytes(head[2:4], "little")
            body = self._recv_exact(length) if length else b""
            if body is None:
                return
            try:
                header, data = enc.decode(head + body)
            except enc.EncapsulationError:
                return
            reply = enip.handle_packet(header, data)
            if reply.delay and self.server.stopping.wait(reply.delay):
                return
            if reply.data is not None:
                try:
                    self.request.sendall(reply.data)
                except OSError:
                    return
            if reply.close:
                return


class _TcpServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"

    def __init__(self, address, enip: Server):
        self.address_family = socket.getaddrinfo(address[0], address[1],
                                                 type=socket.SOCK_STREAM)[0][0]
        self.enip = enip
        self.connections: set[socket.socket] = set()
        self.stopping = threading.Event()
        super().__init__(address, _TcpHandler)
