# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## 0.2.0 - 2026-11-15

### Added

- **Implicit (Class 1) I/O** over UDP 2222: a scanner opens an exclusive-owner or an
  input-only (heartbeat) connection with Forward_Open or Large_Forward_Open, then the
  output data O→T (with the run/idle header) and the input data T→O (modeless) are
  exchanged cyclically at the RPIs, point-to-point
- Run/idle: idle holds the outputs. Connection timeout (multiplier × O→T RPI): the
  connection closes and an exclusive owner's outputs go to zero
- Forward_Open validation with the extended status codes of the specification:
  duplicate connection (0x0100), transport/trigger (0x0103), ownership conflict
  (0x0106), connection type (0x0108), connection size with the expected size (0x0109),
  RPI below 10 ms (0x0111), application path (0x0117), configuration path (0x0118)
- While an exclusive owner runs, explicit writes to the output assembly answer device
  state conflict (0x10)
- T→O sockaddr item in the Forward_Open (where the scanner wants the data) and an O→T
  sockaddr item in the reply when the device's UDP port is not 2222
- Faults: `connection_timeout` (production stops) and `io_loss` (every n-th T→O packet
  is lost); `wrong_assembly` also corrupts T→O data
- Originator side for tests and trials: `EnipClient.open_io()` / `Bench.open_io()`
  returning an `IOConnection`, `ForwardOpenError` with the extended status, and
  `enip-sim io` on the command line; `serve --io-port`
- Profiles: `io:` with `config_instance` and `heartbeat_instance`
- Docker image exposes 2222/udp; the CI runs a Class 1 connection into the container

### Fixed

- Large_Forward_Open read the RPIs at the offsets of a normal Forward_Open, so the
  connection got wrong packet intervals

## 0.1.0 - 2026-11-12

- EtherNet/IP encapsulation over TCP port 44818: RegisterSession / UnRegisterSession,
  ListIdentity, ListServices, ListInterfaces, NOP, SendRRData and SendUnitData, with the
  Common Packet Format
- CIP explicit messaging: Get_Attribute_Single, Get_Attributes_All, Set_Attribute_Single,
  Reset and Unconnected_Send, with logical EPATH segments and the general status codes of
  the specification
- Connected (Class 3) explicit messaging: Forward_Open and Forward_Close, then messages
  over SendUnitData
- Standard CIP objects on every device: Identity, Message Router, Assembly, Connection
  Manager, TCP/IP Interface and Ethernet Link
- Bundled profiles with behaviour: `drive` (an AC drive that ramps to its speed reference
  and latches a trip), `io-block` (a loopback I/O block) and `generic-adapter`
- YAML profiles, including a `profile:` base with overrides for identity, model and network
- Fault injection (`no_response`, `delay`, `drop_connection`, `refuse`,
  `forward_open_reject`, `bad_status`, `reject_write`, `wrong_assembly`, `trip`) through a
  JSON control channel and the command line
- Command line (`serve`, `profiles`, `info`, `identity`, `read`, `write`, `assembly`,
  `inject`, `clear`, `get`, `list`) and a small Python client
- pytest plugin with the `enip_bench` and `enip_bench_factory` fixtures
- Docker image on `ghcr.io/steitec/enip-sim` with a health check
- Test suite cross-checks the simulator against pycomm3 (unconnected and connected reads,
  List Identity)

Implicit (Class 1) I/O over UDP 2222 is planned for the next release.
