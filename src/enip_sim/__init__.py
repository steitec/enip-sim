"""Simulated EtherNet/IP (CIP) devices with behaviour and fault injection.

Public API: :class:`~enip_sim.bench.Bench`, :class:`~enip_sim.device.Device`,
:class:`~enip_sim.client.EnipClient` (with ``open_io`` for Class 1 I/O), and :func:`~enip_sim.profile.build_device`.
"""
__version__ = "0.2.0"

from .bench import Bench, BenchError
from .client import EnipClient, EnipError, ForwardOpenError
from .device import Device
from .originator import IOConnection
from .profile import ProfileError, build_device, bundled_profiles

__all__ = ["Bench", "BenchError", "Device", "EnipClient", "EnipError", "ForwardOpenError",
           "IOConnection", "ProfileError", "build_device", "bundled_profiles", "__version__"]
