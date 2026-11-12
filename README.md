<a href="https://steitec.net"><picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/steitec/.github/main/brand/steitec-logo-on-dark.svg">
  <img align="right" height="32" alt="steitec" src="https://raw.githubusercontent.com/steitec/.github/main/brand/steitec-logo-on-light.svg">
</picture></a>

# enip-sim

[![CI](https://github.com/steitec/enip-sim/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/steitec/enip-sim/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/enip-sim?v=0.1.0)](https://pypi.org/project/enip-sim/)
[![Python](https://img.shields.io/pypi/pyversions/enip-sim?v=0.1.0)](https://pypi.org/project/enip-sim/)
[![License](https://img.shields.io/pypi/l/enip-sim?v=0.1.0)](https://github.com/steitec/enip-sim/blob/main/LICENSE)

Simulated **EtherNet/IP (CIP)** devices that **behave**: an AC drive whose motor ramps
up when you set the run bit in its output assembly and trips when you tell it to, an
I/O block that mirrors its channels, a bare adapter to build your own on. They speak
the EtherNet/IP encapsulation protocol and CIP explicit messaging — unconnected and
connected (Forward_Open / Class 3) — so you can develop and test SCADA links, HMIs,
gateways or your own scanner code without a PLC or a drive on the bench, also in CI.

The test suite cross-checks the simulator against
[pycomm3](https://github.com/ottowayi/pycomm3), so code written against a real client
works against the simulator.

Walkthroughs, CI recipes and troubleshooting will live in the
[wiki](https://github.com/steitec/enip-sim/wiki); this README is the reference.

```console
$ enip-sim serve drive &
enip-sim: ethernet/ip 127.0.0.1:44818, control on :44819
  enip-sim drive
$ enip-sim identity                                   # List Identity of the running device
enip-sim drive
  vendor 0x0001  device type 0x02  product code 0x0193  revision 1.1  serial 0x00000001
$ enip-sim write 0x04 20 3 0100f401                   # output assembly: run forward, 50.0 Hz
$ enip-sim assembly 70                                # two seconds later: the input assembly
assembly 70: 09001401
  status 0x0009 (running, at reference), speed 50.0 Hz
$ enip-sim inject trip code=7                         # fault injection over the control channel
$ enip-sim assembly 70
assembly 70: 04000000
  status 0x0004 (faulted), speed 0.0 Hz
$ enip-sim clear
```

## Why

Free EtherNet/IP simulators give you a static set of attributes. Real integrations
break on other things:

- **behaviour**: a drive needs a logic command and a ramp before it reports a speed;
  code that only ever saw a frozen input assembly has never been tested. A trip must
  latch until a fault reset.
- **the protocol itself**: the encapsulation session (RegisterSession before any
  message), the Common Packet Format, Get/Set_Attribute_Single vs. Get_Attributes_All,
  logical EPATH segments, and connected messaging that needs a Forward_Open first.
- **failures**: devices that do not answer, answer late, drop the TCP connection, refuse
  a Forward_Open, answer a CIP error status, or send a corrupted input assembly. And
  device faults: a drive trip.

enip-sim gives you all of that from a YAML profile, switchable at runtime.

## Install

Requires Python 3.10 or newer; the only runtime dependency is PyYAML.

```bash
pip install enip-sim
```

From source, with the test dependencies (including pycomm3 for the cross-checks):

```bash
git clone https://github.com/steitec/enip-sim
cd enip-sim
pip install -e '.[dev]'
pytest
```

## Quick start

```bash
enip-sim profiles                       # the bundled profiles
enip-sim info drive                     # a profile's identity and assemblies
enip-sim serve drive                    # one drive on EtherNet/IP 127.0.0.1:44818
enip-sim serve drive --host 0.0.0.0     # reachable from the network
enip-sim serve --bench bench.yaml       # a device described by a YAML file
```

Port 44818 is the registered EtherNet/IP port and needs no privileges. The control
channel (default 44819, loopback, no authentication) is for fault injection; turn it
off with `--no-control`.

## Profiles

| profile | what it is | assemblies |
|---|---|---|
| `drive` | AC drive (PowerFlex-style): logic command + speed reference in, status word + actual speed out; ramps to the reference and latches a trip until a fault reset | output 20 (O→T), input 70 (T→O), 4 bytes each |
| `io-block` | discrete/analogue I/O block; by default the inputs mirror the outputs (loopback) | output 101, input 100 |
| `generic-adapter` | a bare adapter with the required objects and no behaviour model, for explicit-messaging tests and as a starting point | — |

```console
$ enip-sim info drive
drive: enip-sim drive
  vendor 0x0001  device type 0x02  product code 0x0193  revision 1.1
  assemblies: 20, 70
  model: DriveModel
```

Every device carries the standard CIP objects a scanner expects: **Identity** (0x01),
**Message Router** (0x02), **Assembly** (0x04), **Connection Manager** (0x06),
**TCP/IP Interface** (0xF5) and **Ethernet Link** (0xF6).

### The drive's assemblies

The output assembly (instance 20, written by the scanner) is a 16-bit **logic command**
followed by a 16-bit **speed reference** in units of 0.1 Hz:

| logic command bit | meaning |
|---|---|
| 0 | run forward |
| 1 | run reverse |
| 2 | fault reset (rising edge, while stopped) |

The input assembly (instance 70, produced by the drive) is a 16-bit **status word**
followed by the 16-bit **actual speed** (0.1 Hz):

| status word bit | meaning |
|---|---|
| 0 | running |
| 1 | ready |
| 2 | faulted |
| 3 | at reference |
| 4 | running reverse |

### Your own profiles

A profile is a YAML file. Reference a bundled profile with `profile:` and override
parts of it, or describe a device from scratch:

```yaml
# fast-drive.yaml
profile: drive
name: conveyor
identity:
  product_name: enip-sim conveyor drive
  serial: 0x00001234
model:
  accel_time: 2.0
  decel_time: 2.0
  max_speed: 60.0
network:
  ip: 192.168.1.50
  mac: 00:1D:9C:12:34:56
```

```bash
enip-sim serve --bench fast-drive.yaml
```

## Protocol

enip-sim implements the EtherNet/IP encapsulation layer and CIP explicit messaging:

| layer | what |
|---|---|
| encapsulation | RegisterSession / UnRegisterSession, ListIdentity, ListServices, ListInterfaces, NOP, SendRRData, SendUnitData |
| CIP services | Get_Attribute_Single, Get_Attributes_All, Set_Attribute_Single, Reset; Unconnected_Send |
| connections | Forward_Open / Forward_Close for connected (Class 3) explicit messaging |

Messages come in unconnected (SendRRData, optionally wrapped in an Unconnected_Send) or
connected (a Forward_Open, then SendUnitData). A request to an unknown class or instance
answers *path destination unknown* (0x05); an unknown attribute, *attribute not
supported* (0x14); a write to a read-only attribute, *attribute not settable* (0x0E).

## Fault injection

Inject faults into a running device with `enip-sim inject FAULT [name=value ...]`
over the control channel, clear them with `enip-sim clear [FAULT]`:

| fault | parameters | effect |
|---|---|---|
| `no_response` | count=1 | no answer; the scanner times out |
| `delay` | seconds | answers come late |
| `drop_connection` | count=1 | the TCP connection is closed instead of answering |
| `refuse` | | new TCP connections are closed at once |
| `forward_open_reject` | status=0x0100 | Forward_Open answers a connection failure with this extended status |
| `bad_status` | status=0x02, service | a CIP service answers this general status (optionally only one service) |
| `reject_write` | status=0x0F | Set_Attribute answers this status |
| `wrong_assembly` | | the input assembly reads corrupted |
| `trip` | code=1 | `drive`: fault trip, motor coasts to stop, latched until a fault reset |

## Control channel

`enip-sim serve` also listens on a control port (default 44819, loopback only, no
authentication). It takes one JSON object per line:

```json
{"inject": "trip", "params": {"code": 7}}
{"clear": "trip"}
{"get": ["speed", "faulted"]}
{"list": true}
```

`enip-sim inject`, `clear`, `get` and `list` are the command-line front for it.

## Python client

A minimal client is included, mainly for trying things out and for tests:

```python
from enip_sim import EnipClient
from enip_sim import cip

with EnipClient("127.0.0.1", 44818) as c:
    print(c.list_identity()["product_name"])
    c.set_attribute_single(cip.ASSEMBLY, 20, 3, b"\x01\x00\xf4\x01")   # run, 50.0 Hz
    status_speed = c.get_attribute_single(cip.ASSEMBLY, 70, 3)
```

A CIP error raises `EnipError` (with `.status`). For production code use pycomm3 or a
similar full stack; the test suite cross-checks the simulator against pycomm3.

## pytest plugin

Installing enip-sim registers two fixtures:

```python
def test_my_scanner(enip_bench):                  # one drive, free port
    enip_bench.inject("trip", code=4)
    with enip_bench.client() as c:
        ...

def test_line(enip_bench_factory):
    io = enip_bench_factory("io-block")            # or a bench file, or a dict
    with io.client() as c:
        ...
```

`--enip-bench FILE` or the ini key `enip_bench` sets what `enip_bench` starts. See
[examples/test_example.py](examples/test_example.py).

## Docker

For scanners that are not written in Python: the image runs the `drive` profile on
EtherNet/IP, with the control channel for fault injection.

```bash
docker run --rm --name sim -p 44818:44818 -p 127.0.0.1:44819:44819 ghcr.io/steitec/enip-sim
```

The control channel has no authentication, hence `127.0.0.1:` in front of it. The image
reports itself healthy once port 44818 accepts connections, so a service container in
GitHub Actions starts with the device ready:

```yaml
services:
  enip:
    image: ghcr.io/steitec/enip-sim
    ports:
      - 44818:44818
      - 127.0.0.1:44819:44819
```

## Limitations

- For development and testing; not a certified or conformance-tested EtherNet/IP
  implementation.
- The bundled devices are invented, with plausible assemblies and behaviour; they are
  not copies of any product. Product names identify a documented style only.
- **No implicit (Class 1) I/O yet** — no cyclic O→T / T→O exchange over UDP 2222. That
  is the headline of the next release. This version is explicit messaging (unconnected
  and connected) only.
- No CIP routing through a backplane (port segments / slots), no Large Forward_Open
  connection parameters beyond what is parsed, no EtherNet/IP Security (CIP Security).
- One device per server (an EtherNet/IP device is one network endpoint).

## Licence

MIT, see [LICENSE](LICENSE).

EtherNet/IP and CIP are trademarks of ODVA, Inc. steitec is not affiliated with ODVA.
