# Native QQ egress policy boundary — offline primitive only

## Status and scope

`tools/qq_egress_policy.py` is a pure, reusable destination policy parser. It does
**not** isolate QQ, enforce network access, resolve DNS, create sockets/listeners,
activate interfaces, modify firewalls, or launch QQ. It is not wired into any QQ
transport or the existing plugin HTTP-fetch helpers. Those unrelated helpers are
unchanged; this is not an SSRF migration or an endpoint allowlist.

The reported Ubuntu-TSC availability check found `ip` and `unshare`, but not
`slirp4netns`, `pasta`, `nft`, or `iptables` via `which`. This is an availability
observation, not proof that no implementation could exist elsewhere. **No actual
usable userspace networking/proxy enforcement component has been established.**
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
