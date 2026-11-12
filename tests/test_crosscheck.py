"""Cross-check the simulator against pycomm3, a third-party EtherNet/IP client."""
from __future__ import annotations

import pytest

from enip_sim import objects as obj
from enip_sim.device import Device
from enip_sim.server import Server

pycomm3 = pytest.importorskip("pycomm3")
from pycomm3 import CIPDriver  # noqa: E402
from pycomm3.cip import Services, UINT  # noqa: E402


@pytest.fixture
def driver():
    identity = obj.identity_object(vendor_id=0x004B, device_type=0x02, product_code=0x1234,
                                   major=2, minor=7, status=0x0030, serial=1,
                                   product_name="enip-sim test")
    assembly = obj.assembly_object(instance=100, size=4, data=b"\x01\x02\x03\x04")
    server = Server(Device("dev", [identity, assembly]), host="127.0.0.1", port=0)
    port = server.start()
    try:
        with CIPDriver(f"127.0.0.1:{port}") as drv:
            yield drv
    finally:
        server.stop()


def test_pycomm3_reads_vendor_id(driver):
    result = driver.generic_message(service=Services.get_attribute_single,
                                    class_code=0x01, instance=1, attribute=1,
                                    connected=False, unconnected_send=False,
                                    data_type=UINT, name="vendor")
    assert result.error is None
    assert result.value == 0x004B


def test_pycomm3_reads_assembly(driver):
    result = driver.generic_message(service=Services.get_attribute_single,
                                    class_code=0x04, instance=100, attribute=3,
                                    connected=False, unconnected_send=False, name="asm")
    assert result.error is None
    assert result.value == b"\x01\x02\x03\x04"


def test_pycomm3_connected_read(driver):
    # a Class 3 connection: pycomm3 does Forward_Open then reads over the connection
    result = driver.generic_message(service=Services.get_attribute_single,
                                    class_code=0x01, instance=1, attribute=1,
                                    connected=True, data_type=UINT, name="vendor")
    assert result.error is None
    assert result.value == 0x004B


def test_pycomm3_list_identity(driver):
    identity = driver.list_identity(f"{driver._cfg['ip address']}:{driver._cfg['port']}")
    assert identity is not None
    assert identity["product_name"] == "enip-sim test"
    assert identity["product_code"] == 0x1234
    assert identity["revision"] == {"major": 2, "minor": 7}
