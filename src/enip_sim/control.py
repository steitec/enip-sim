"""Control channel of a running bench: inject faults and read device state from outside.

One JSON object per line in, one per line out. ``device`` is optional (a bench serves one
device)::

    {"inject": "trip", "params": {"code": 7}}            -> {"ok": true}
    {"clear": true}                 (or "clear": "trip")  -> {"ok": true}
    {"get": true}                   (or ["speed", ...])   -> {"ok": true, "values": {...}}
    {"list": true}  -> {"ok": true, "devices": {"pump": {"faults": [...], "requests": 12}}}

Anything invalid gets ``{"ok": false, "error": "..."}``; the connection stays open. The
channel has no authentication, so bind it to loopback.
"""
from __future__ import annotations

import json
import os
import socket
import socketserver
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .bench import Bench
    from .device import Device

CONTROL_PORT = 44819
_MAX_LINE = 65536


class _Handler(socketserver.StreamRequestHandler):
    def setup(self) -> None:
        super().setup()
        self.server.connections.add(self.request)

    def finish(self) -> None:
        self.server.connections.discard(self.request)
        super().finish()

    def handle(self) -> None:
        try:
            while True:
                raw = self.rfile.readline(_MAX_LINE)
                if not raw:
                    return
                if len(raw) >= _MAX_LINE and not raw.endswith(b"\n"):
                    self.wfile.write(json.dumps({"ok": False, "error": "line too long"})
                                     .encode() + b"\n")
                    self.wfile.flush()
                    self.request.shutdown(socket.SHUT_WR)
                    return
                if not raw.strip():
                    continue
                answer = self.server.control.handle(raw)
                self.wfile.write(json.dumps(answer).encode() + b"\n")
        except OSError:
            return


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"

    def __init__(self, address, handler):
        self.address_family = socket.getaddrinfo(address[0], address[1],
                                                 type=socket.SOCK_STREAM)[0][0]
        self.connections: set[socket.socket] = set()
        super().__init__(address, handler)


class ControlServer:
    def __init__(self, bench: "Bench", host: str = "127.0.0.1", port: int = CONTROL_PORT):
        self.bench, self.host, self.port = bench, host, port
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> int:
        self._server = _Server((self.host, self.port), _Handler)
        self._server.control = self
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        kwargs={"poll_interval": 0.05}, daemon=True,
                                        name="enip-sim-control")
        self._thread.start()
        return self.port

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        for connection in list(self._server.connections):
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self._thread.join(timeout=5)
        self._server = self._thread = None

    def handle(self, raw: bytes | str) -> dict[str, Any]:
        try:
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError("a request is a JSON object")
            return self._handle(request)
        except ValueError as error:          # includes JSONDecodeError and FaultError
            return {"ok": False, "error": str(error)}
        except Exception as error:           # never drop the connection with a traceback
            return {"ok": False, "error": f"{type(error).__name__}: {error}"}

    def _handle(self, request: dict) -> dict[str, Any]:
        if request.get("list"):
            return {"ok": True, "devices": {name: self._describe(dev)
                                            for name, dev in self.bench.devices.items()}}
        device = self.bench.find(request.get("device"))
        if request.get("clear"):
            kind = request["clear"]
            if kind is True:
                device.clear()
            elif isinstance(kind, str):
                device.clear(kind)
            else:
                raise ValueError("'clear' must be true or a fault name")
        elif "inject" in request:
            kind, params = request["inject"], request.get("params") or {}
            if not isinstance(kind, str):
                raise ValueError("'inject' must be a fault name")
            if not isinstance(params, dict):
                raise ValueError("'params' must be an object")
            device.inject(kind, **params)
        elif "get" in request:
            return {"ok": True, "values": self._get(device, request["get"])}
        else:
            raise ValueError("a request needs 'list', 'clear', 'inject' or 'get'")
        return {"ok": True}

    @staticmethod
    def _get(device: "Device", names: Any) -> dict[str, Any]:
        variables = device.model.variables() if device.model is not None else {}
        if names is True:
            return variables
        if not isinstance(names, list):
            raise ValueError("'get' must be true or a list of variable names")
        missing = [n for n in names if n not in variables]
        if missing:
            raise ValueError(f"unknown variable(s) {', '.join(map(str, missing))}; "
                             f"have: {', '.join(variables) or 'none'}")
        return {name: variables[name] for name in names}

    @staticmethod
    def _describe(device: "Device") -> dict[str, Any]:
        return {"faults": device.faults.active(), "requests": device.requests,
                "model": device.model.variables() if device.model is not None else None}


def send_control(request: dict, host: str = "127.0.0.1", port: int = CONTROL_PORT,
                 timeout: float = 2.0) -> dict:
    """Send one request to a bench's control port; raises OSError if unreachable."""
    with socket.create_connection((host, port), timeout=timeout) as sock, \
            sock.makefile("rwb") as stream:
        stream.write(json.dumps(request).encode() + b"\n")
        stream.flush()
        line = stream.readline()
    if not line:
        raise OSError("control port closed the connection")
    return json.loads(line)
