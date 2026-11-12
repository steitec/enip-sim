"""Shared socket helpers for the server tests (raw EtherNet/IP over a socket)."""
from __future__ import annotations

import socket

from enip_sim import encapsulation as enc


def recv_packet(sock) -> bytes:
    header = b""
    while len(header) < enc.HEADER_LEN:
        chunk = sock.recv(enc.HEADER_LEN - len(header))
        if not chunk:
            raise ConnectionError("connection closed before a full header")
        header += chunk
    length = int.from_bytes(header[2:4], "little")
    body = b""
    while len(body) < length:
        chunk = sock.recv(length - len(body))
        if not chunk:
            raise ConnectionError("connection closed before the full body")
        body += chunk
    return header + body


def register_session(sock) -> int:
    sock.sendall(enc.encode(enc.REGISTER_SESSION, data=bytes([1, 0, 0, 0])))
    header, _ = enc.decode(recv_packet(sock))
    return header.session


def exchange(port, command, data=b"", session=0):
    with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
        if command != enc.REGISTER_SESSION and session == 0:
            session = register_session(sock)
        sock.sendall(enc.encode(command, session=session, data=data))
        return enc.decode(recv_packet(sock))
