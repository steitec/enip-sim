"""Load a device profile (YAML or dict) and build a wired :class:`~enip_sim.device.Device`.

A profile names the Identity attributes, an optional behaviour model and the network
identity. The builder creates the required CIP objects, the model and its input/output
assemblies, and binds the assemblies to the model's buffers.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from . import cip, objects
from .device import Device
from .models import DriveModel, IOBlockModel

_PROFILE_DIR = Path(__file__).with_name("profiles")


class ProfileError(ValueError):
    """An unknown profile or an invalid profile description."""


def bundled_profiles() -> list[str]:
    return sorted(p.stem for p in _PROFILE_DIR.glob("*.yaml"))


def load_profile(source: str | Path | dict) -> dict[str, Any]:
    """Return the profile dict for a bundled name, a YAML path, or a dict."""
    if isinstance(source, dict):
        return source
    name = str(source)
    if name in bundled_profiles():
        path = _PROFILE_DIR / f"{name}.yaml"
    else:
        path = Path(source)
        if not path.is_file():
            raise ProfileError(
                f"unknown profile {name!r}; bundled: {', '.join(bundled_profiles())}")
    try:
        with path.open(encoding="utf-8") as handle:
            spec = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as error:
        raise ProfileError(f"cannot read profile {path}: {error}") from None
    if not isinstance(spec, dict):
        raise ProfileError(f"profile {path} is not a mapping")
    return spec


def _build_model(spec: dict[str, Any] | None):
    if not spec:
        return None
    params = dict(spec)
    kind = params.pop("type", None)
    try:
        if kind == "drive":
            return DriveModel(**params)
        if kind == "io-block":
            return IOBlockModel(**params)
    except (TypeError, ValueError) as error:
        raise ProfileError(f"bad parameters for model {kind!r}: {error}") from None
    raise ProfileError(f"unknown model type {kind!r}")


def _identity(spec: dict[str, Any]) -> objects.CIPObject:
    ident = spec.get("identity", {})
    revision = ident.get("revision", {"major": 1, "minor": 0})
    return objects.identity_object(
        vendor_id=int(ident.get("vendor_id", 0x0001)),
        device_type=int(ident.get("device_type", 0x00)),
        product_code=int(ident.get("product_code", 0x0001)),
        major=int(revision.get("major", 1)),
        minor=int(revision.get("minor", 0)),
        status=int(ident.get("status", 0x0030)),
        serial=int(ident.get("serial", 0x00000001)),
        product_name=str(ident.get("product_name", "enip-sim device")))


def _assemblies(model) -> list[objects.CIPObject]:
    if model is None:
        return []

    def reader(m):
        return lambda: bytes(m.input)

    def output_reader(m):
        return lambda: bytes(m.output)

    def writer(m):
        return lambda value: m.output.__setitem__(slice(None), value)

    return [
        objects.assembly_object(instance=model.OUTPUT_INSTANCE, size=len(model.output),
                                settable=True, getter=output_reader(model),
                                setter=writer(model)),
        objects.assembly_object(instance=model.INPUT_INSTANCE, size=len(model.input),
                                settable=False, getter=reader(model)),
    ]


def _merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Shallow merge, one level deep for the nested identity/model/network mappings."""
    result = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = {**result[key], **value}
        else:
            result[key] = value
    return result


def build_device(source: str | Path | dict, name: str | None = None) -> Device:
    """Build a fully wired Device from a bundled profile, a YAML path, or a dict.

    A dict (or YAML) may name a base with ``profile:`` and override parts of it."""
    spec = load_profile(source)
    if "profile" in spec:
        overrides = {k: v for k, v in spec.items() if k != "profile"}
        spec = _merge(load_profile(spec["profile"]), overrides)
    model = _build_model(spec.get("model"))
    network = spec.get("network", {})
    mac = network.get("mac", "00:00:00:00:00:00")
    mac_bytes = bytes(int(part, 16) for part in str(mac).replace("-", ":").split(":")[:6])
    all_objects = [
        _identity(spec),
        objects.message_router_object([cip.IDENTITY, cip.MESSAGE_ROUTER, cip.ASSEMBLY,
                                       cip.CONNECTION_MANAGER, cip.TCPIP_INTERFACE,
                                       cip.ETHERNET_LINK]),
        objects.connection_manager_object(),
        objects.tcpip_interface_object(ip=network.get("ip", "0.0.0.0"),
                                       host_name=network.get("host_name", "")),
        objects.ethernet_link_object(speed=int(network.get("speed", 100)), mac=mac_bytes),
        *_assemblies(model),
    ]
    return Device(name or spec.get("name", "device"), all_objects, model=model)
