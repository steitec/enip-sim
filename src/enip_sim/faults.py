"""Fault injection.

A :class:`FaultSet` holds the transport and CIP faults that a device currently carries.
The server and the device call :meth:`FaultSet.take` while answering a request; a fault
with a finite ``count`` fires that many times, otherwise it stays until cleared. Model
faults (``trip``, ``stuck_input``) act on the behaviour model directly and are applied by
the device, not stored here.
"""
from __future__ import annotations

import math
from typing import Any

# transport faults, consulted by the server
SERVER = {
    "delay": {"seconds": 0.5},            # answers come late
    "no_response": {},                    # no answer at all (the scanner times out)
    "drop_connection": {},                # the TCP connection is closed instead of answering
    "refuse": {},                         # new TCP connections are closed at once
    "forward_open_reject": {"status": 0x0100},   # Forward_Open answers this extended status
    "connection_timeout": {},             # an established implicit connection goes quiet
}
# CIP faults, consulted by the device
DEVICE = {
    "bad_status": {"status": 0x02, "service": None},   # a service answers this general status
    "reject_write": {"status": 0x0F},                  # Set_Attribute answers this status
    "wrong_assembly": {},                              # the input assembly reads corrupted
}
#: faults that act on the behaviour model, applied by the device
MODEL_FAULTS = {"trip", "stuck_input"}

# faults that fire once by default; everything else stays until cleared
_DEFAULT_COUNT = {"no_response": 1, "drop_connection": 1}


class FaultError(ValueError):
    """An unknown fault kind or parameter."""


def _catalog(kind: str) -> dict[str, Any] | None:
    if kind in SERVER:
        return SERVER[kind]
    if kind in DEVICE:
        return DEVICE[kind]
    return None


class FaultSet:
    def __init__(self) -> None:
        self._faults: dict[str, dict[str, Any]] = {}

    def inject(self, kind: str, *, count: int | None = None, **params: Any) -> None:
        defaults = _catalog(kind)
        if defaults is None:
            known = ", ".join(sorted(set(SERVER) | set(DEVICE) | MODEL_FAULTS))
            raise FaultError(f"unknown fault {kind!r}; known: {known}")
        unknown = set(params) - set(defaults)
        if unknown:
            raise FaultError(f"{kind}: unknown parameter(s) {', '.join(sorted(unknown))}")
        full = {**defaults, **params}
        if kind == "delay":
            seconds = full["seconds"]
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) \
                    or not math.isfinite(seconds) or seconds < 0:
                raise FaultError("delay seconds must be a non-negative number")
        remaining = count if count is not None else _DEFAULT_COUNT.get(kind)
        if remaining is not None and remaining <= 0:
            raise FaultError("count must be positive")
        self._faults[kind] = {"params": full, "count": remaining}

    def take(self, kind: str) -> dict[str, Any] | None:
        entry = self._faults.get(kind)
        if entry is None:
            return None
        if entry["count"] is not None:
            entry["count"] -= 1
            if entry["count"] <= 0:
                del self._faults[kind]
        return dict(entry["params"])

    def clear(self, kind: str | None = None) -> None:
        if kind is None:
            self._faults.clear()
        else:
            self._faults.pop(kind, None)

    def active(self) -> list[str]:
        return sorted(self._faults)


def check_model_fault(kind: str, params: dict[str, Any]) -> dict[str, Any]:
    """Validate a model fault's parameters and return them filled in."""
    if kind == "trip":
        code = params.get("code", 1)
        if isinstance(code, bool) or not isinstance(code, int):
            raise FaultError("trip code must be an integer")
        extra = set(params) - {"code"}
        if extra:
            raise FaultError(f"trip: unknown parameter(s) {', '.join(sorted(extra))}")
        return {"code": code}
    if kind == "stuck_input":
        if params:
            raise FaultError(f"stuck_input: unknown parameter(s) {', '.join(sorted(params))}")
        return {}
    raise FaultError(f"unknown model fault {kind!r}")
