"""The bundled EtherNet/IP client, exercised against the simulator's own server."""
from __future__ import annotations

import struct
import time

import pytest

from enip_sim import cip
from enip_sim import profile as prof
from enip_sim.client import EnipClient, EnipError
from enip_sim.server import Server


@pytest.fixture
def client():
    server = Server(prof.build_device("drive"), host="127.0.0.1", port=0)
    port = server.start()
    try:
        with EnipClient("127.0.0.1", port) as c:
            yield c
    finally:
        server.stop()


def test_get_attribute_single_reads_vendor_id(client):
    data = client.get_attribute_single(cip.IDENTITY, 1, 1)
    assert struct.unpack("<H", data)[0] == 0x0001


def test_set_then_get_drive_assembly_runs_the_motor(client):
    client.set_attribute_single(cip.ASSEMBLY, 20, 3, struct.pack("<HH", 0x0001, 500))
    time.sleep(1.0)
    status_speed = client.get_attribute_single(cip.ASSEMBLY, 70, 3)
    status_word, speed = struct.unpack("<HH", status_speed)
    assert status_word & 0x0001          # running
    assert speed > 50                    # 0.1 Hz units -> > 5 Hz


def test_cip_error_raises(client):
    with pytest.raises(EnipError) as info:
        client.get_attribute_single(cip.IDENTITY, 1, 99)
    assert info.value.status == cip.ATTRIBUTE_NOT_SUPPORTED


def test_list_identity_product_name(client):
    identity = client.list_identity()
    assert identity["product_name"].startswith("enip-sim")
    assert identity["product_code"] == 0x0193
