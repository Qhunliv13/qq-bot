# Native QQ egress policy boundary — offline primitive only

## Status and scope

`tools/qq_egress_policy.py` is a pure, reusable destination policy parser. It does
**not** isolate QQ, enforce network access, resolve DNS, create sockets/listeners,
activate interfaces, modify firewalls, or launch QQ. It is not wired into any QQ
transport or the existing plugin HTTP-fetch helpers. Those unrelated helpers are
unchanged; this is not an SSRF migration or an endpoint allowlist.

The earlier Ubuntu-TSC availability check found `ip` and `unshare`, but not
`slirp4netns`, `pasta`, `nft`, or `iptables` via `which`. After explicit user consent,
`slirp4netns` and `nftables` plus their three required libraries were installed
from the configured Ubuntu archive mirror (see installation evidence below).
**An integrated userspace network exit for QQ has not yet been established.**
Network-isolated testing remains required; authorization to test an independent
QQ instance is not authorization to bypass isolation.

## Pure API contract

```python
from tools.qq_egress_policy import validate_resolved_destination

plan = validate_resolved_destination(
    "example.com", 443,
    ["8.8.8.8", "2001:4860:4860::8888"],
    allowed_ports={443},
)
# plan.host == "example.com" (context only, not evidence of DNS ownership)
# plan.port == 443
# plan.addresses == ("8.8.8.8", "2001:4860:4860::8888")
```

These are offline sample values, not QQ endpoints and not a claim that these
addresses serve that hostname. The function accepts data, never resolver callbacks.
It returns a frozen `DestinationPlan` with canonical host context, exact integer
port, and an immutable tuple of canonical literal IP strings. Invalid input raises
`ValueError`; no partial plan is returned.

- Ports must be exact `int` values in 1..65535 (`bool` is rejected). Optional
  `allowed_ports` must be a set/frozenset of such integers; empty means deny all;
  `None` deliberately permits any valid port. Caller chooses policy: no baked-in
  ports, QQ destinations, environment configuration, or privileged-port assumptions.
- Answers must be a concrete list/tuple of **1–16 entries before deduplication**.
  Every answer must pass. Mixed public/private answers fail as a whole, never filter.
  Duplicates are removed in first-seen order after validation.
- A literal host must itself pass and every answer must equal it. IPv4 spelling
  must be canonical dotted decimal. IPv6 is compressed/lowercased for the plan.
  Integer/bytes objects, scope zones, brackets, CIDR suffixes, whitespace, octal,
  short IPv4, whole decimal and hexadecimal IPv4 aliases are rejected.
- Hostnames are bare strict DNS labels with canonical lowercase stdlib IDNA
  encoding and reversible round-trip checks. Credentials, URL/port/path/query
  syntax, empty labels, trailing dots, underscores, invalid A-labels, lossy IDNA
  mappings (including alternate dots/fullwidth numeric input) are rejected.
  This intentionally conservative contract is not a complete browser URL parser
  or an IDNA2008 implementation. Hostname context does not authenticate supplied
  DNS answers and never authorizes connecting by hostname later.
- Addresses require `is_global` and reject private, reserved, loopback, link-local,
  multicast and unspecified flags. Explicit conservative IPv4 CIDRs also reject
  special-use, documentation, CGNAT, benchmarking and deprecated relay ranges.
  IPv6 must be within native `2000::/3` and outside explicit exclusions, including
  all `2001::/23` (conservatively excluding even globally reachable exceptions),
  `2001:db8::/32`, `3fff::/20`, `2002::/16`, mapped/compatible IPv4, Teredo,
  NAT64 `64:ff9b::/96` and `64:ff9b:1::/48`. Explicit bounds protect against known
  older Python classification gaps; this is not a timeless registry snapshot.
  A future registry change may require policy review.

## Required future enforcement — not implemented here

1. QQ must remain in a private network namespace. A userspace network or proxy
   gate must be the **only** egress route, fail closed, and prevent alternate direct
   IPv4/IPv6 paths and inherited network descriptors from escaping the boundary.
2. The local bridge/gateway must be verified to deny access to host loopback,
   host-local services, private/link-local networks and metadata endpoints, including
   gateway aliases and host forwarding paths. Public IP classification alone says
   nothing about local routing/NAT, a host-owned public IP, or subnet-directed
   broadcast: those require actual route/interface-aware enforcement and tests.
3. A connector must consume only `plan.addresses` and `plan.port` as literal socket
   destinations; `plan.host` can supply validated TLS SNI/HTTP context, never a new
   resolver/authority argument. Reconnects, redirects and proxy destination changes
   require fresh complete validation. No DNS re-resolution between check and use;
   numeric-only connection and peer verification need real integration tests.
4. DNS resolution must happen through a controlled path outside the isolated client,
   with the entire bounded answer set validated before connection. Arbitrary client
   DNS must not bypass the gate. UDP, QUIC and any other transports must either use
   equivalent pinned enforcement or remain blocked. A TCP-only proxy is not complete
   UDP or DNS isolation. Network-specific translation (including arbitrary NAT64
   prefixes) cannot be identified exhaustively by this parser alone.
5. Demonstrate negative tests for host loopback/bridge aliases, LAN/metadata,
   IPv4/IPv6, UDP/DNS bypass, mixed answers, rebinding and check/use races before
   enabling any actual QQ traffic. None of that enforcement is claimed here.

## Offline checks

From the repository root, the new standalone stdlib test module can be run without
pytest, QQ or any network activity:

```text
python -m unittest discover -s tests -p test_qq_egress_policy.py -v
```

It also fits pytest collection when already installed. Only this test module is
needed for this change; do not infer full-suite or real-network validation from it.

The same 11 stdlib test methods passed under Windows Python 3.13.12 and the
existing Ubuntu-TSC Python 3.12 environment (Linux run: 0.016s). The Linux command
used an absolute test directory; no dependencies, resolver, listener, network
configuration or QQ execution were involved. This verifies the tested address
matrix across those interpreters, not every current or future address registry
entry or any runtime enforcement.

## Explicit pinned TCP connector (not activated)

`tools/qq_egress_connector.py` adds `connect_pinned(plan, *, timeout_seconds,
allowed_ports, address_index=0, socket_factory=socket.socket,
clock=time.monotonic, stop_event=None)`. Import does not open a socket. Only an
explicit call connects; nothing invokes this from QQ, a proxy, or a listener.
The preceding future-enforcement checklist still applies: this is a connector
primitive, **not a complete bridge, network isolation, or UDP/DNS enforcement**.

- A frozen `DestinationPlan` can be forged with its public constructor. The
  connector therefore requires the exact plan type, reuses
  `validate_resolved_destination` on **all fields and all answers**, and requires
  canonical immutable fields matching the resulting snapshot. It rejects duplicate
  or noncanonical plans instead of silently changing the selected address index.
- `allowed_ports` has **no default**. The trusted caller must pass the same bound
  used for policy validation, or explicitly pass `None` for unrestricted valid
  ports. A plan does not remember that bound; the connector cannot recover it.
- Only the exact integer `address_index` (default 0) is attempted, once. There are
  no automatic retries, other-address fallback, aliases, redirects, or proxy use.
  `ipaddress` selects AF_INET/AF_INET6, fixed-family `inet_pton` checks the literal,
  and `socket(family, SOCK_STREAM, IPPROTO_TCP).connect((literal, port))` uses that
  canonical numeric string only. `host` is never a connect/resolver argument.
  No `getaddrinfo` or `socket.create_connection` is used.
- Exact int/float timeout values must be in **1..30 seconds**, excluding bool and
  nonfinite values. A monotonic deadline starts before plan validation; remaining
  time is set immediately before connect. Late returns, including after peer
  checks, are rejected and closed. This is synchronous: it cannot preempt a
  blocking trusted callback or OS scheduling delay. `stop_event` optionally accepts
  a trusted `threading.Event`, checked before connection and after connect/peer
  verification; setting it cannot interrupt the in-progress syscall immediately.
- Peer identity requires an exact tuple (IPv4 length 2; IPv6 length 4), a numeric
  string canonically equal to the selected IP, exact integer matching port, and
  zero integer IPv6 flowinfo/scopeid. Scoped and IPv4-mapped IPv6 peers fail.
- Success returns the **raw socket**, owned by the trusted caller, which must close
  it or use its native context manager. The remaining connect timeout stays set;
  it is not a deadline across subsequent reads/writes. There is no TLS, credential
  snapshot, authentication, payload send, or transport protocol implementation.
- Every allocated failure socket receives a close attempt, including on interrupts.
  Fixed `ConnectorError.code` values are `invalid_plan`, `invalid_timeout`,
  `invalid_dependency`, `invalid_stop_event`, `invalid_address_index`, `clock_failed`,
  `cancelled`, `deadline_exceeded`, `factory_failed`, `connect_failed`, `peer_failed`,
  `peer_mismatch`, and `close_failed`; exception messages contain only the code and
  underlying exception display is suppressed. If close itself fails, release is
  **not established** and `close_failed` is raised; BaseException interrupts still
  propagate after attempted cleanup. No raw network error is logged.
- Factory/clock/event injections and the returned socket owner are trusted, not a
  sandbox for hostile Python code. The factory must return a fresh, unconnected
  socket and must not resolve names or conceal peer identity. The deadline cannot
  enforce the behavior of a malicious or indefinitely blocking factory.

A host-owned public IP, local routes/NAT, gateway aliases, or a public IP routed to
an internal service remain outside this primitive's protection. No service binds
or listener starts here; no claim is made that an external service is unreachable.
Real socket/resolver integration and route/interface-aware negative tests remain
outstanding before enabling actual QQ traffic. The existing pure policy is unchanged.

Focused offline tests (fake factories only, resolver/create-connection entrypoints
forbidden by spies):

```text
python -m unittest discover -s tests -p 'test_qq_egress*.py' -v
```

The connector tests cover both families, forgery, bounds, one-address selection,
peer failures/mismatch, timeout/cancellation, cleanup and success ownership. They
perform no actual external network calls and do not constitute production or
complete-bridge validation.

## Real kernel verification inside an offline namespace

The explicit [POSIX fixture](../tests/egress_connector_posix_check.py) now exercises
the unchanged connector with real Linux TCP sockets, not substituted sockets. It
requires a trusted launcher's parent namespace identity and refuses the parent or
visible init namespace. Before any `ip` command it requires only DOWN loopback and
no usable routes. Only the new namespace receives two fixed public-looking test
literals on loopback. A second check requires exact addresses and local routes,
with no gateway or other interface, before accepting or connecting any TCP socket.
The addresses are local test fixtures, not QQ endpoints or public destinations.

The first [observation](qq-egress-kernel-evidence.json) stopped at `initial_routes`
before configuration or TCP: this WSL kernel represents an empty IPv4 route table
as zero bytes, rather than a header. Read-only inspection established that format.
The narrow correction accepts exactly empty text; whitespace/malformed nonempty
text and usable routes still fail. The failed artifact remains unchanged.

The separately reserved [second observation](qq-egress-kernel-v2-evidence.json)
passed with exit 0 and stderr length 0: two real IPv4/IPv6 exchanges, six owned
exchange sockets closed, one refused connection closed without fallback, two
preallocation policy rejections and zero Python resolver-helper calls. Socket
family, actual peer address/port, IPv6 scope and retained timeouts were checked.
The spy does not intercept CPython's C implementation or prove arbitrary resolver
behavior; the actual numeric-connection path was exercised. An outer 20-second
watchdog owns the test process group; namespace lifetime ends with its processes.

These tests establish only local kernel integration. They do not provide an
external gateway, route-aware host/LAN denial, UDP/DNS isolation, QQ connectivity,
login or feature acceptance. No host interface, firewall, production process or
QQ session was modified. The 18 guard tests run offline with substituted OS
observations; the explicit kernel fixture is not automatically launched by pytest.

## Authorized WSL package installation

The user explicitly approved test dependency installation, not QQ network launch
or production replacement. [Installation evidence](qq-lab-network-packages-evidence.json)
records the five installed packages and downloaded archive SHA256 values:
slirp4netns 1.2.1-1build2, nftables/libnftables1 1.0.9-1ubuntu0.1,
libslirp0 4.7.0-1ubuntu3.1, and libnftnl11 1.2.6-2build1. The simulation and actual
install added five packages, upgraded none and removed none. The test distribution
reports Ubuntu 24.04.5 LTS. Its configured Tsinghua Ubuntu mirror uses the Ubuntu
archive keyring; insecure repository options were false and no authentication
bypass was supplied. Archives were downloaded first and maintainer scripts reviewed
before fixed-version `--no-download --no-install-recommends` installation.

The nftables first-install script does not start the service; its restart clause
applies only to upgrades, and this WSL has no systemd init. `slirp4netns --version`
reports 1.2.1 with libslirp 4.7.0; its help advertises disable-host-loopback,
disable-dns, sandbox and seccomp options. Those option names are not evidence that
a bridge was started or that all host/LAN bypasses are prevented. No slirp bridge
or QQ client was started during installation.

## Real namespace-only firewall enforcement check

[Explicit nft fixture](../tests/egress_nft_posix_check.py) reuses the prior namespace
configuration and real kernel baseline. Only inside that fresh namespace it creates
one fixed inet output chain with drop policy and four counted IPv4/IPv6 TCP/UDP
drop rules. It does not expose arbitrary rule input, modify host rules, or start
slirp/QQ. [Mocked unit tests](../tests/test_egress_nft_posix_check.py) check ordering,
fixed commands and strict counter identity, and do not run nft automatically.

The [initial run](qq-egress-nft-kernel-evidence.json) passed the TCP baseline but
failed at an unclassified negative probe. A [diagnostic-only revision](qq-egress-nft-kernel-v2-evidence.json)
identified UDP IPv4 send returning errno 1 (EPERM). Neither artifact was rewritten.
Linux local OUTPUT drop can fail the send syscall rather than report successful
send followed by receive timeout. The final test added successful UDP delivery
baselines for both families, permits only that observed EPERM after rules, still
requires receive timeout, and requires all four exact drop counters to be positive.
Other send errors, delivered packets or missing counters remain failures.

The [third run](qq-egress-nft-kernel-v3-evidence.json) passed, exit 0/stderr 0:
IPv4/IPv6 TCP and UDP baselines succeeded; two TCP connections timed out and their
allocations closed; both UDP sends returned EPERM with no received datagram; each
of the four rules counted exactly one packet. Read-only parent ruleset hashes
matched before/after every run ([first](qq-egress-nft-host-comparison.json),
[second](qq-egress-nft-host-v2-comparison.json), [third](qq-egress-nft-host-v3-comparison.json)).
This comparison covers the tests, not a retroactive pre-installation ruleset hash.

This proves the installed WSL kernel can enforce the tested all-drop rules in an
isolated namespace. It does **not** verify selective public egress, slirp gateway
aliases, host/LAN exclusion under NAT, controlled DNS or a connected QQ client.
Those integrated boundaries and real independent-account login remain pending.

## Real slirp lifecycle under nested offline containment

The [explicit slirp fixture](../tests/egress_slirp_posix_check.py) starts inside a
fresh outer network namespace accepted by the existing only-DOWN-loopback/no-route
guard. Its inner helper creates a second private namespace and installs the same
four-rule default-DROP output chain before sending READY. The shared nft validator
now supports an explicit strictly boolean `require_positive=False` for nonnegative
counts; its default still requires positive counts. The readiness gate additionally
requires every packet and byte count to be zero, so this mode is not mistaken for
observed packet blocking.

Before attaching slirp, the parent verifies its owned helper PID is alive and in
a namespace distinct from outer, launcher and visible init. The actual fixed
slirp command enables configure, disable-host-loopback, disable-dns, sandbox and
seccomp; only ready/exit descriptors are inherited. Both children stay in the
outer driver's process group. The parent waits for the real readiness byte and
requires slirp to remain alive. The helper verifies only lo/tap0, the expected
private IPv4 interface and gateway, no routable IPv6, and unchanged DROP rules.
A single UDP attempt is then denied with EPERM and increments the IPv4 UDP counter.
No packet can reach an external network: the slirp process itself is also inside
the offline outer namespace. This does not test internet forwarding or upstream DNS.

[Actual evidence](qq-slirp-offline-evidence.json): exit 0, bridge started/ready,
DROP verified before and after attach, IPv4 UDP counter 1 with the other three
counters 0, and both slirp/helper cleanly retired. The exit pipe EOF terminates
slirp; each owned child is waited/reaped. A forced kill would not count as clean
retirement. The [parent ruleset hashes](qq-slirp-host-comparison.json) matched before
and after. The test neither starts QQ nor supplies account data.

This establishes real bridge lifecycle and deny-all containment, not selective
public egress or complete native QQ readiness. Host-loopback/DNS-disable flags
were passed but their protection is not independently proven by this offline
setup; host/LAN bypass testing under forwarding remains necessary. Exact network
values are fixed fixture expectations, not runtime QQ endpoint configuration.
