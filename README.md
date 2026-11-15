<a href="https://steitec.net"><picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/steitec/.github/main/brand/steitec-logo-on-dark.svg">
  <img align="right" height="32" alt="steitec" src="https://raw.githubusercontent.com/steitec/.github/main/brand/steitec-logo-on-light.svg">
</picture></a>

# enip-sim

[![CI](https://github.com/steitec/enip-sim/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/steitec/enip-sim/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/enip-sim?v=0.2.0)](https://pypi.org/project/enip-sim/)
[![Python](https://img.shields.io/pypi/pyversions/enip-sim?v=0.2.0)](https://pypi.org/project/enip-sim/)
[![License](https://img.shields.io/pypi/l/enip-sim?v=0.2.0)](https://github.com/steitec/enip-sim/blob/main/LICENSE)

Simulated **EtherNet/IP (CIP)** devices that **behave**: an AC drive whose motor ramps
up when you set the run bit in its output assembly and trips when you tell it to, an
I/O block that mirrors its channels, a bare adapter to build your own on. They speak
the EtherNet/IP encapsulation protocol, CIP explicit messaging — unconnected and
connected (Forward_Open / Class 3) — and **implicit (Class 1) I/O**: the cyclic exchange
of assembly data over UDP 2222 that scanners use to run a device. So you can develop and
test SCADA links, HMIs, gateways or your own scanner code without a PLC or a drive on the
bench, also in CI: connection setup, run/idle, timeouts, lost packets and the rejections
you meet at commissioning.

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
| connections | Forward_Open / Large_Forward_Open / Forward_Close: connected (Class 3) explicit messaging and implicit (Class 1) I/O |
| implicit I/O | cyclic O→T / T→O assembly data over UDP 2222, point-to-point, see [Implicit I/O](#implicit-class-1-io) |

Messages come in unconnected (SendRRData, optionally wrapped in an Unconnected_Send) or
connected (a Forward_Open, then SendUnitData). A request to an unknown class or instance
answers *path destination unknown* (0x05); an unknown attribute, *attribute not
supported* (0x14); a write to a read-only attribute, *attribute not settable* (0x0E).

## Implicit (Class 1) I/O

A scanner opens a Class 1 connection with Forward_Open (or Large_Forward_Open) over TCP.
From then on it sends its output data O→T to the device's UDP port 2222 at its RPI, and
the device produces its input data T→O at the T→O RPI. That is what keeps a drive
running on a real network, and what most scanner, gateway and HMI code depends on.

| | |
|---|---|
| connection types | **exclusive owner** (O→T = output assembly) and **input-only** (O→T = heartbeat instance 198); point-to-point both ways, cyclic trigger |
| connection path | `20 04 24 <config> 2C <O→T point> 2C <T→O point>` (port segments, an electronic key and config data are accepted and skipped; the key is not checked) |
| real-time format | O→T with the 32-bit run/idle header, T→O modeless; both with the 16-bit sequence count |
| sizes (network connection parameters) | O→T = output assembly + 6 bytes, T→O = input assembly + 2 bytes; heartbeat O→T 2 or 6 |
| RPI | 10 ms or slower; the answer's API equals the requested RPI |
| run / idle | run: the outputs go to the output assembly; idle: the device holds its outputs |
| timeout | no O→T packet for timeout multiplier × O→T RPI: the connection closes, production stops and an exclusive owner's outputs go to **zero** (safe state). Forward_Close leaves them as they are |
| T→O destination | the scanner's IP address (the TCP peer), port from a T→O sockaddr item in the Forward_Open, else 2222 |
| UDP port | 2222 (`serve --io-port N`); a port other than 2222 is announced in an O→T sockaddr item in the reply |

While an exclusive owner runs, an explicit write to the output assembly answers *device
state conflict* (0x10), as on a real adapter. A Forward_Open the device cannot accept gets
a connection failure (0x01) with the extended status you would see at commissioning:

| extended status | when |
|---|---|
| 0x0100 | the same connection (serial, vendor, originator serial) is already open |
| 0x0103 | not Class 1 cyclic or Class 3 (e.g. change-of-state trigger) |
| 0x0106 | an exclusive owner is already connected to the output assembly |
| 0x0108 | multicast or null connection type |
| 0x0109 | wrong connection size; the additional status word is the size the device expects |
| 0x0111 | RPI below 10 ms |
| 0x0117 | unknown O→T or T→O connection point (or a device without I/O assemblies) |
| 0x0118 | wrong configuration instance |

The I/O points of the bundled profiles:

| profile | config | O→T (output) | T→O (input) | heartbeat |
|---|---|---|---|---|
| `drive` | 1 | 20 (4 bytes) | 70 (4 bytes) | 198 |
| `io-block` | 102 | 101 (2 bytes) | 100 (2 bytes) | 198 |

A profile can change the config and heartbeat instances with
`io: {config_instance: 5, heartbeat_instance: 199}`. `generic-adapter` has no assemblies,
so it rejects Class 1 connections.

From the command line, `enip-sim io` opens a connection and prints the inputs whenever
they change (the profile comes from the device type, or `--profile`):

```console
$ enip-sim io --out 0100f401 --rpi 20 --seconds 3       # run forward, 50.0 Hz
enip-sim: drive I/O open (run, RPI 20 ms), O->T 0x20000001, T->O 0x31389027
  0.000 s  in 02000000  status 0x0002 (ready), speed 0.0 Hz
  0.040 s  in 03000500  status 0x0003 (running, ready), speed 0.5 Hz
  0.101 s  in 03000a00  status 0x0003 (running, ready), speed 1.0 Hz
  ...
enip-sim: 151 input packets in 3 s
```

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
| `wrong_assembly` | | the input assembly reads corrupted (explicit reads and T→O data) |
| `connection_timeout` | | Class 1 connections stop producing T→O data; the scanner's connection times out |
| `io_loss` | every=2 | every n-th T→O packet of each Class 1 connection is lost |
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

The client can also act as the originator of a Class 1 connection:

```python
with EnipClient("127.0.0.1", 44818) as c:
    with c.open_io(output_instance=20, input_instance=70, config_instance=1,
                   output_size=4, input_size=4, rpi_ms=20) as io:
        io.set_outputs(b"\x01\x00\xf4\x01")          # run forward, 50.0 Hz
        io.run()                                       # run bit set (io.idle() clears it)
        io.wait_for_inputs(lambda d: d[2:] == b"\xf4\x01", timeout=15)
```

A rejected Forward_Open raises `ForwardOpenError` with `.extended` (e.g. 0x0109).

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

A bench opens Class 1 connections with its device's own I/O points:

```python
def test_scanner_sees_the_trip(enip_bench):
    with enip_bench.open_io(rpi_ms=20) as io:
        io.set_outputs(b"\x01\x00\xf4\x01")
        io.run()
        enip_bench.inject("trip", code=4)
        io.wait_for_inputs(lambda d: d[0] & 0x04)      # status: faulted
```

The fixtures start on free ports, UDP included (`bench.io_port`).
`--enip-bench FILE` or the ini key `enip_bench` sets what `enip_bench` starts. See
[examples/test_example.py](examples/test_example.py).

## Docker

For scanners that are not written in Python: the image runs the `drive` profile on
EtherNet/IP, with the control channel for fault injection.

```bash
docker run --rm --name sim -p 44818:44818 -p 2222:2222/udp -p 127.0.0.1:44819:44819 \
    ghcr.io/steitec/enip-sim
```

The control channel has no authentication, hence `127.0.0.1:` in front of it. Class 1
I/O needs UDP both ways: the device sends T→O to the scanner's address as it sees it,
which works with Docker Engine on Linux (the CI checks it) but not through Docker
Desktop's VM. If the scanner itself listens on UDP 2222 on the same machine, give the
simulator another UDP port (`serve --io-port 2223`, published as `-p 2223:2223/udp`); the
reply to Forward_Open tells the scanner where to send. The image
reports itself healthy once port 44818 accepts connections, so a service container in
GitHub Actions starts with the device ready:

```yaml
services:
  enip:
    image: ghcr.io/steitec/enip-sim
    ports:
      - 44818:44818
      - 2222:2222/udp
      - 127.0.0.1:44819:44819
```

## Limitations

- For development and testing; not a certified or conformance-tested EtherNet/IP
  implementation.
- The bundled devices are invented, with plausible assemblies and behaviour; they are
  not copies of any product. Product names identify a documented style only.
- Implicit I/O is point-to-point and cyclic only: no multicast T→O, no listen-only
  connections, no change-of-state or application trigger. The RPI is 10 ms or slower,
  with the timing jitter of a Python process.
- The scanner must send O→T from the IP address its TCP session comes from; packets from
  any other address are ignored.
- No CIP routing through a backplane (port segments / slots), no EtherNet/IP Security
  (CIP Security).
- One device per server (an EtherNet/IP device is one network endpoint).

## Licence

MIT, see [LICENSE](LICENSE).

EtherNet/IP and CIP are trademarks of ODVA, Inc. steitec is not affiliated with ODVA.
