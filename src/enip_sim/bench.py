"""A bench ties one device to its EtherNet/IP server and an optional control channel.

An EtherNet/IP device is one network endpoint, so a bench hosts a single device on one
TCP port. Build one from a bundled profile, a YAML file or a dict, start it, and reach
it with :meth:`client` or any EtherNet/IP tool.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .client import EnipClient
from .device import Device
from .profile import ProfileError, build_device
from .server import TCP_PORT, Server


class BenchError(RuntimeError):
    """The bench could not be built or started."""


class Bench:
    def __init__(self, device: Device):
        self.device = device
        self.host = "127.0.0.1"
        self.port = TCP_PORT
        self.control_host = "127.0.0.1"
        self.control_port: int | None = None
        self._server: Server | None = None
        self._control = None

    # --- construction --------------------------------------------------------------
    @classmethod
    def single(cls, profile: str | Path, name: str | None = None) -> "Bench":
        try:
            return cls(build_device(profile, name=name))
        except ProfileError as error:
            raise BenchError(str(error)) from None

    @classmethod
    def from_dict(cls, spec: dict[str, Any]) -> "Bench":
        try:
            return cls(build_device(spec))
        except ProfileError as error:
            raise BenchError(str(error)) from None

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Bench":
        try:
            return cls(build_device(Path(path)))
        except ProfileError as error:
            raise BenchError(str(error)) from None

    # --- lifecycle -----------------------------------------------------------------
    def start(self, *, host: str = "127.0.0.1", port: int = TCP_PORT,
              control_host: str = "127.0.0.1", control_port: int | None = None) -> int:
        self.host, self.port = host, port
        self._server = Server(self.device, host=host, port=port)
        try:
            self.port = self._server.start()
        except OSError as error:
            self._server = None
            raise BenchError(f"cannot bind {host}:{port}: {error}") from None
        if control_port is not None:
            from .control import ControlServer
            self._control = ControlServer(self, host=control_host, port=control_port)
            self.control_host = control_host
            self.control_port = self._control.start()
        return self.port

    def stop(self) -> None:
        if self._control is not None:
            self._control.stop()
            self._control = None
        if self._server is not None:
            self._server.stop()
            self._server = None

    # --- access --------------------------------------------------------------------
    def client(self) -> EnipClient:
        """An EtherNet/IP client for this bench; use it as a context manager, or
        call ``.connect()`` yourself."""
        return EnipClient(self.host, self.port)

    @property
    def devices(self) -> dict[str, Device]:
        return {self.device.name: self.device}

    def find(self, name: Any = None) -> Device:
        if name is None or name == self.device.name:
            return self.device
        raise ValueError(f"no device {name!r}; this bench serves {self.device.name!r}")

    def inject(self, kind: str, **params: Any) -> None:
        self.device.inject(kind, **params)

    def clear(self, kind: str | None = None) -> None:
        self.device.clear(kind)

    def variables(self) -> dict[str, Any]:
        model = self.device.model
        return model.variables() if model is not None else {}

    def describe(self) -> str:
        where = self._server.describe() if self._server is not None else "not started"
        return f"{self.device.name}: {where}"
