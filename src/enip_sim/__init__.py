"""Simulated EtherNet/IP (CIP) devices with behaviour and fault injection.

Public API: :class:`~enip_sim.bench.Bench`, :class:`~enip_sim.device.Device`,
:class:`~enip_sim.client.EnipClient`, and :func:`~enip_sim.profile.build_device`.
"""
__version__ = "0.1.0"

from .bench import Bench, BenchError
from .client import EnipClient, EnipError
from .device import Device
from .profile import ProfileError, build_device, bundled_profiles

__all__ = ["Bench", "BenchError", "Device", "EnipClient", "EnipError", "ProfileError",
           "build_device", "bundled_profiles", "__version__"]
