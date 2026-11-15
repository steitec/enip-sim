"""Implicit (Class 1) I/O: the cyclic exchange of assembly data over UDP.

A Class 1 connection is opened with Forward_Open over TCP (see :mod:`.connection`).
From then on the originator (scanner) sends its output data O->T to UDP port 2222 at
its RPI, with a 32-bit run/idle header, and the device produces its input data T->O at
the T->O RPI, without a header. Both carry a 16-bit CIP sequence count; the packets are
Common Packet Format with a sequenced address item and a connected data item.

One :class:`IOEngine` serves all Class 1 connections of a device: one thread receives,
one thread produces for every connection at its own RPI. When no O->T packet arrives
for the connection timeout (timeout multiplier x O->T RPI), the connection closes and
an exclusive owner's outputs go to zero, as a real adapter falls back to a safe state.
"""
from __future__ import annotations

import heapq
import os
import socket
import struct
import threading
import time
from dataclasses import dataclass

from . import encapsulation as enc
from .connection import O2T_HEADER, SEQ_COUNT, Connection, ConnectionManager

IO_PORT = 2222
_RECV_POLL = 0.05       # s; how often the receive thread looks at the stop flag


@dataclass
class IOPacket:
    cid: int
    enc_seq: int
    cip_seq: int
    header: int | None      # the 32-bit run/idle header, or None (modeless)
    data: bytes


def encode_io_packet(cid: int, enc_seq: int, cip_seq: int, data: bytes, *,
                     run: bool | None = None) -> bytes:
    """A Class 1 datagram. ``run=None`` is modeless (T->O); True/False add the run/idle
    header (O->T)."""
    payload = struct.pack("<H", cip_seq & 0xFFFF)
    if run is not None:
        payload += struct.pack("<I", 1 if run else 0)
    address = struct.pack("<II", cid & 0xFFFFFFFF, enc_seq & 0xFFFFFFFF)
    return enc.encode_cpf([(enc.ITEM_SEQUENCED_ADDRESS, address),
                           (enc.ITEM_CONNECTED_DATA, payload + bytes(data))])


def decode_io_packet(buffer: bytes, *, with_header: bool) -> IOPacket:
    """Decode a Class 1 datagram; raises EncapsulationError when it is not one."""
    address = payload = None
    for type_id, data in enc.decode_cpf(buffer):
        if type_id == enc.ITEM_SEQUENCED_ADDRESS and len(data) == 8:
            address = struct.unpack("<II", data)
        elif type_id == enc.ITEM_CONNECTED_DATA:
            payload = data
    minimum = SEQ_COUNT + (O2T_HEADER if with_header else 0)
    if address is None or payload is None or len(payload) < minimum:
        raise enc.EncapsulationError("not a Class 1 I/O datagram")
    (cip_seq,) = struct.unpack_from("<H", payload, 0)
    header = struct.unpack_from("<I", payload, 2)[0] if with_header else None
    return IOPacket(address[0], address[1], cip_seq, header, payload[minimum:])


@dataclass
class _State:
    connection: Connection
    enc_seq: int = 0
    cip_seq: int = 0
    produced: int = 0
    due: float = 0.0


class IOEngine:
    def __init__(self, device, connections: ConnectionManager, *,
                 host: str = "127.0.0.1", port: int = IO_PORT):
        self.device = device
        self.connections = connections
        self.host = host
        self.port = port
        self.threads: list[threading.Thread] = []
        self._states: dict[int, _State] = {}          # by O->T connection id
        self._heap: list[tuple[float, int]] = []
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._sock: socket.socket | None = None

    # --- lifecycle -----------------------------------------------------------------
    def start(self) -> int:
        family = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_DGRAM)[0][0]
        sock = socket.socket(family, socket.SOCK_DGRAM)
        if os.name != "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.settimeout(_RECV_POLL)
        self._sock = sock
        self.port = sock.getsockname()[1]
        self._stop.clear()
        self.threads = [
            threading.Thread(target=self._receive, daemon=True, name="enip-sim-io-rx"),
            threading.Thread(target=self._produce, daemon=True, name="enip-sim-io-tx"),
        ]
        for thread in self.threads:
            thread.start()
        return self.port

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        for thread in self.threads:
            thread.join(timeout=5)
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        with self._lock:
            self._states.clear()
            self._heap.clear()

    # --- connections ---------------------------------------------------------------
    def add(self, connection: Connection) -> None:
        """Start serving a Class 1 connection (from ConnectionManager.on_open)."""
        if not connection.is_io:
            return
        now = time.monotonic()
        connection.last_rx = now          # the timeout starts with the open
        with self._lock:
            self._states[connection.ot_cid] = _State(connection, due=now)
            heapq.heappush(self._heap, (now, connection.ot_cid))
            if connection.kind == "exclusive":
                self.device.owned_output = True
        self._wake.set()

    def remove(self, connection: Connection, reason: str) -> None:
        """Stop serving a connection (from ConnectionManager.on_close)."""
        with self._lock:
            if self._states.pop(connection.ot_cid, None) is None:
                return
            if connection.kind == "exclusive":
                self.device.owned_output = False
                if reason == "timeout":
                    self.device.write_outputs(b"")      # safe state: all zero

    # --- threads -------------------------------------------------------------------
    def _receive(self) -> None:
        while not self._stop.is_set():
            try:
                data, address = self._sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    return
                continue
            self._consume(data, address)

    def _consume(self, data: bytes, address) -> None:
        try:
            packet = decode_io_packet(data, with_header=False)
        except enc.EncapsulationError:
            return
        with self._lock:
            state = self._states.get(packet.cid)
            if state is None:
                return
            connection = state.connection
            if connection.peer is None or address[0] != connection.peer[0]:
                return
            body = packet.data
            if connection.ot_size >= SEQ_COUNT + O2T_HEADER:     # run/idle header
                if len(body) < O2T_HEADER:
                    return
                connection.run = bool(struct.unpack_from("<I", body, 0)[0] & 1)
                body = body[O2T_HEADER:]
            connection.last_rx = time.monotonic()
            if connection.kind == "exclusive" and connection.run:
                expected = connection.ot_size - SEQ_COUNT - O2T_HEADER
                if len(body) == expected:
                    self.device.write_outputs(body)

    def _produce(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()
            timed_out: list[Connection] = []
            with self._lock:
                while self._heap and self._heap[0][0] <= now:
                    due, cid = heapq.heappop(self._heap)
                    state = self._states.get(cid)
                    if state is None or state.due != due:
                        continue                      # closed, or rescheduled
                    connection = state.connection
                    limit = (4 << connection.timeout_multiplier) * connection.ot_rpi / 1e6
                    if now - connection.last_rx > limit:
                        timed_out.append(connection)
                        continue
                    self._send(state)
                    interval = connection.to_rpi / 1e6
                    state.due = due + interval
                    if state.due < now:               # fell behind: do not burst
                        state.due = now + interval
                    heapq.heappush(self._heap, (state.due, cid))
                next_due = self._heap[0][0] if self._heap else None
            for connection in timed_out:
                self.connections.close(connection, "timeout")
            wait = _RECV_POLL if next_due is None else max(0.0, next_due - time.monotonic())
            if self._wake.wait(wait):
                self._wake.clear()

    def _send(self, state: _State) -> None:
        faults = self.device.faults
        state.enc_seq += 1
        state.cip_seq = (state.cip_seq + 1) & 0xFFFF
        state.produced += 1
        if faults.take("connection_timeout") is not None:
            return
        loss = faults.take("io_loss")
        if loss is not None and state.produced % loss["every"] == 0:
            return
        connection = state.connection
        packet = encode_io_packet(connection.to_cid, state.enc_seq, state.cip_seq,
                                  self.device.read_inputs())
        try:
            self._sock.sendto(packet, connection.peer)
        except (OSError, TypeError):
            pass                                      # unreachable scanner: keep going
