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


def ncp16(*, size: int, conn_type: int = 2, fixed: bool = True, priority: int = 0) -> int:
    """16-bit network connection parameters (Forward_Open)."""
    return ((conn_type & 3) << 13) | ((priority & 3) << 10) | ((0 if fixed else 1) << 9) \
        | (size & 0x1FF)


def ncp32(*, size: int, conn_type: int = 2, fixed: bool = True, priority: int = 0) -> int:
    """32-bit network connection parameters (Large_Forward_Open)."""
    return ((conn_type & 3) << 29) | ((priority & 3) << 26) | ((0 if fixed else 1) << 25) \
        | (size & 0xFFFF)


def io_path(*, config: int, consumed: int, produced: int) -> bytes:
    """Connection path to the assembly object: config instance + two connection points."""
    return bytes([0x20, 0x04, 0x24, config, 0x2C, consumed, 0x2C, produced])


def forward_open_data(*, path: bytes, to_cid: int = 0x11111111, serial: int = 0x1234,
                      vendor: int = 0x004B, orig_serial: int = 0xDEADBEEF,
                      multiplier: int = 2, ot_rpi: int = 50000, to_rpi: int = 50000,
                      ot_ncp: int | None = None, to_ncp: int | None = None,
                      transport: int = 0x01, large: bool = False) -> bytes:
    """Forward_Open (or Large_Forward_Open) request data, everything configurable."""
    import struct
    if ot_ncp is None:
        ot_ncp = (ncp32 if large else ncp16)(size=2 + 4 + 2)
    if to_ncp is None:
        to_ncp = (ncp32 if large else ncp16)(size=2 + 2)
    ncp_fmt = "<I" if large else "<H"
    if len(path) % 2:
        path += b"\x00"
    return (bytes([0x0A, 0x0E])
            + struct.pack("<II", 0, to_cid)
            + struct.pack("<HHI", serial, vendor, orig_serial)
            + bytes([multiplier, 0, 0, 0])
            + struct.pack("<I", ot_rpi) + struct.pack(ncp_fmt, ot_ncp)
            + struct.pack("<I", to_rpi) + struct.pack(ncp_fmt, to_ncp)
            + bytes([transport, len(path) // 2]) + path)
