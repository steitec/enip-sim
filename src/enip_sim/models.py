"""Behaviour models that drive the I/O assemblies.

A model owns an output assembly buffer (O->T, written by the scanner) and an input
assembly buffer (T->O, produced by the device). ``tick(dt)`` reads the output, advances
the state and refreshes the input. Models are plausible, not exact copies of any
product; a drive ramps towards its reference and latches a trip, an I/O block mirrors
its channels.
"""
from __future__ import annotations

import struct


class DriveModel:
    """An AC drive. Output assembly: logic command (UINT) + speed reference (0.1 Hz,
    UINT). Input assembly: status word (UINT) + actual speed (0.1 Hz, UINT).

    Logic command bits: 0 run forward, 1 run reverse, 2 fault reset (rising edge).
    Status word bits: 0 running, 1 ready, 2 faulted, 3 at reference.
    """

    OUTPUT_INSTANCE = 20
    INPUT_INSTANCE = 70
    OUTPUT_SIZE = 4
    INPUT_SIZE = 4

    def __init__(self, *, max_speed: float = 50.0, accel_time: float = 5.0,
                 decel_time: float = 5.0, time_scale: float = 1.0):
        if max_speed <= 0:
            raise ValueError("max_speed must be positive")
        self.max_speed = float(max_speed)
        self.accel_time = max(float(accel_time), 1e-3)
        self.decel_time = max(float(decel_time), 1e-3)
        self.time_scale = float(time_scale)
        self.output = bytearray(self.OUTPUT_SIZE)
        self.input = bytearray(self.INPUT_SIZE)
        self.speed = 0.0            # actual output frequency, Hz
        self.reverse = False
        self.faulted = False
        self.fault_code = 0
        self._prev_reset = False
        self._refresh_input(ready=True)

    # --- commands ------------------------------------------------------------------
    def trip(self, code: int = 1) -> None:
        self.faulted = True
        self.fault_code = int(code)
        self.speed = 0.0

    def reset(self) -> None:
        self.faulted = False
        self.fault_code = 0

    # --- behaviour -----------------------------------------------------------------
    def tick(self, dt: float) -> None:
        dt = dt * self.time_scale
        control, reference = struct.unpack_from("<HH", self.output, 0)
        run_fwd = bool(control & 0x0001)
        run_rev = bool(control & 0x0002)
        fault_reset = bool(control & 0x0004)
        running = run_fwd or run_rev

        if fault_reset and not self._prev_reset and self.faulted and not running:
            self.reset()
        self._prev_reset = fault_reset
        self.reverse = run_rev and not run_fwd

        if self.faulted:
            self.speed = 0.0
        else:
            target = (reference * 0.1) if running else 0.0
            target = min(target, self.max_speed)
            if target > self.speed:
                rate = self.max_speed / self.accel_time
                self.speed = min(self.speed + rate * dt, target)
            elif target < self.speed:
                rate = self.max_speed / self.decel_time
                self.speed = max(self.speed - rate * dt, target)

        self._refresh_input(ready=not self.faulted, commanded=running, reference=reference)

    def _refresh_input(self, *, ready: bool, commanded: bool = False,
                       reference: int = 0) -> None:
        status = 0
        if self.speed > 0.01:
            status |= 0x0001                                   # running
        if ready:
            status |= 0x0002                                   # ready
        if self.faulted:
            status |= 0x0004                                   # faulted
        if commanded and abs(self.speed - reference * 0.1) < 0.1:
            status |= 0x0008                                   # at reference
        if self.reverse:
            status |= 0x0010                                   # running reverse
        struct.pack_into("<HH", self.input, 0, status, round(self.speed * 10))

    # --- named variables for the control channel -----------------------------------
    @property
    def running(self) -> bool:
        control = struct.unpack_from("<H", self.output, 0)[0]
        return bool(control & 0x0003) and not self.faulted

    @property
    def status_word(self) -> int:
        return struct.unpack_from("<H", self.input, 0)[0]

    def variables(self) -> dict[str, float]:
        return {"speed": round(self.speed, 2), "faulted": self.faulted,
                "fault_code": self.fault_code, "running": self.running,
                "status_word": self.status_word}


class IOBlockModel:
    """A discrete/analogue I/O block. The output assembly sets the outputs; the input
    assembly reports the inputs. Without configured signals the inputs mirror the
    outputs (a loopback), which is what most bench setups want."""

    OUTPUT_INSTANCE = 101
    INPUT_INSTANCE = 100

    def __init__(self, *, input_size: int = 2, output_size: int = 2,
                 loopback: bool = True, time_scale: float = 1.0):
        self.output = bytearray(output_size)
        self.input = bytearray(input_size)
        self.loopback = loopback
        self.time_scale = float(time_scale)

    def tick(self, dt: float) -> None:
        if self.loopback:
            width = min(len(self.input), len(self.output))
            self.input[:width] = self.output[:width]

    def variables(self) -> dict[str, int]:
        return {"outputs": int.from_bytes(self.output, "little"),
                "inputs": int.from_bytes(self.input, "little")}
