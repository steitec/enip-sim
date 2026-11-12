"""Profiles: load bundled device descriptions and build a wired Device."""
from __future__ import annotations

import struct

import pytest

from enip_sim import cip
from enip_sim import profile as prof
from enip_sim.models import DriveModel


def test_bundled_profiles_present():
    names = prof.bundled_profiles()
    assert {"drive", "io-block", "generic-adapter"} <= set(names)


def test_build_drive_has_identity_and_model():
    device = prof.build_device("drive", name="pump")
    assert device.name == "pump"
    assert isinstance(device.model, DriveModel)
    identity = device.find(cip.IDENTITY, 1)
    status, name_bytes, _ = identity.service(
        cip.GET_ATTRIBUTE_SINGLE, cip.decode_path(cip.encode_path(0x01, 1, 7)), b"")
    assert status == cip.SUCCESS
    assert name_bytes[1:].startswith(b"enip-sim")


def test_drive_output_assembly_drives_the_model():
    device = prof.build_device("drive")
    out = device.model.OUTPUT_INSTANCE
    # write run + reference into the output assembly through CIP
    device.handle(cip.encode_mr_request(cip.SET_ATTRIBUTE_SINGLE,
                                        cip.encode_path(0x04, out, 3),
                                        struct.pack("<HH", 0x0001, 500)))
    for _ in range(20):
        device.tick(0.5)
    assert device.model.speed > 49.0
    # the input assembly now reports the running bit
    reply = device.handle(cip.encode_mr_request(
        cip.GET_ATTRIBUTE_SINGLE, cip.encode_path(0x04, device.model.INPUT_INSTANCE, 3)))
    _, status, _, data = cip.decode_mr_reply(reply)
    assert status == cip.SUCCESS
    assert struct.unpack_from("<H", data, 0)[0] & 0x0001


def test_tcpip_and_ethernet_link_objects_exist():
    device = prof.build_device("generic-adapter")
    assert device.find(cip.TCPIP_INTERFACE, 1) is not None
    assert device.find(cip.ETHERNET_LINK, 1) is not None


def test_unknown_profile_raises():
    with pytest.raises(prof.ProfileError):
        prof.build_device("does-not-exist")
