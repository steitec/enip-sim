# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

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
