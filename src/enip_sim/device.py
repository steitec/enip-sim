"""A simulated EtherNet/IP device: a set of CIP objects plus an optional behaviour model.

The device routes a Message Router request to the object named by the request path and
frames the reply. The server handles sessions, the Common Packet Format and connections;
the device handles CIP object access.
"""
from __future__ import annotations

from typing import Any, Iterable

from . import cip
from .faults import FaultError, FaultSet, MODEL_FAULTS, check_model_fault
from .models import DriveModel
from .objects import CIPObject


class Device:
    def __init__(self, name: str, objects: Iterable[CIPObject], *, model=None):
        self.name = name
        self.objects: dict[tuple[int, int], CIPObject] = {
            (o.class_id, o.instance): o for o in objects}
        self.model = model
        self.requests = 0
        self.faults = FaultSet()

    def find(self, class_id: int, instance: int) -> CIPObject | None:
        return self.objects.get((class_id, instance))

    # --- fault injection -----------------------------------------------------------
    def inject(self, kind: str, **params: Any) -> None:
        if kind in MODEL_FAULTS:
            full = check_model_fault(kind, params)
            if kind == "trip" and isinstance(self.model, DriveModel):
                self.model.trip(full["code"])
            elif kind == "stuck_input":
                setattr(self.model, "loopback", False) if self.model is not None else None
            return
        count = params.pop("count", None)
        self.faults.inject(kind, count=count, **params)

    def clear(self, kind: str | None = None) -> None:
        if kind is None or kind == "trip":
            if isinstance(self.model, DriveModel):
                self.model.reset()
        if kind is None or kind == "stuck_input":
            if self.model is not None and hasattr(self.model, "loopback"):
                self.model.loopback = True
        if kind not in MODEL_FAULTS:
            self.faults.clear(kind)

    def message(self, service: int, path: cip.Path,
                data: bytes) -> tuple[int, bytes, list[int]]:
        """Route one CIP service to its object; unknown path -> destination unknown."""
        if path.class_id is None or path.instance is None:
            return cip.PATH_DESTINATION_UNKNOWN, b"", []
        obj = self.objects.get((path.class_id, path.instance))
        if obj is None:
            return cip.PATH_DESTINATION_UNKNOWN, b"", []
        return obj.service(service, path, data)

    def handle(self, request: bytes) -> bytes:
        """Decode a Message Router request, route it and return the framed reply."""
        self.requests += 1
        try:
            service, path_bytes, data = cip.decode_mr_request(request)
        except cip.CIPError:
            return cip.encode_mr_reply(0x00, cip.PATH_SEGMENT_ERROR)
        try:
            path = cip.decode_path(path_bytes)
        except cip.CIPError:
            return cip.encode_mr_reply(service, cip.PATH_SEGMENT_ERROR)

        bad = self.faults.take("bad_status")
        if bad is not None and (bad["service"] is None or bad["service"] == service):
            return cip.encode_mr_reply(service, bad["status"])
        if service in (cip.SET_ATTRIBUTE_SINGLE, cip.SET_ATTRIBUTES_ALL):
            reject = self.faults.take("reject_write")
            if reject is not None:
                return cip.encode_mr_reply(service, reject["status"])

        status, reply_data, additional = self.message(service, path, data)
        if (status == cip.SUCCESS and service == cip.GET_ATTRIBUTE_SINGLE
                and path.class_id == cip.ASSEMBLY and reply_data
                and self.faults.take("wrong_assembly") is not None):
            reply_data = bytes([reply_data[0] ^ 0xFF]) + reply_data[1:]
        return cip.encode_mr_reply(service, status, additional=additional, data=reply_data)

    def tick(self, dt: float) -> None:
        """Advance the behaviour model and refresh the assemblies it drives."""
        if self.model is not None:
            self.model.tick(dt)
