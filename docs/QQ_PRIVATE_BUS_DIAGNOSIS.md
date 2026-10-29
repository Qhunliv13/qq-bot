# Private bus admission: hypothesis, not a diagnosed root cause

The host [capture evidence](qq-host-dispatch-capture-evidence.json) reports a missing
**system** bus socket and an unknown-scope DBus error. No private-session-path
failure matched. That cannot establish whether the private session bus admitted
the client, or whether any bus issue caused Electron's missing ready event.

## Established boundary

The existing private runtime gate checks socket type, owner and daemon liveness,
not SASL authentication, Hello or GetId. Its tests are mocked isolation/report tests,
not a real handshake. The jail intentionally has fresh writable HOME/config/cache
and font configuration, without host passwd/group, NSS configuration or machine ID.
A read-only package query found D-Bus 1.14.10-4ubuntu4.1 and dbus-send installed in
the WSL lab. The upstream analysis below is not proof of Ubuntu's patched binary.

## Upstream code separates two stages

In upstream D-Bus 1.14.10, numeric EXTERNAL identity parsing has a numeric UID fast
path that does not require an existing passwd entry. Therefore **do not claim
EXTERNAL inherently requires /etc/passwd**.

However, the daemon's Unix-user authorization callback leads to
`bus_policy_allow_unix_user`, which looks up groups for the UID before the
same-owner default decision. The upstream user database initialization/get-UID
path uses getpwuid/getpwuid_r and can fail when that identity is unresolvable.
This makes post-SASL admission failure a plausible hypothesis for the private
jail, not an observed result. NSS can supply identity by other mechanisms; this
also does not establish that a separate group file is mandatory.

Sources: [auth](https://gitlab.freedesktop.org/dbus/dbus/-/raw/dbus-1.14.10/dbus/dbus-auth.c),
[user database](https://gitlab.freedesktop.org/dbus/dbus/-/raw/dbus-1.14.10/dbus/dbus-userdb.c),
[group lookup](https://gitlab.freedesktop.org/dbus/dbus/-/raw/dbus-1.14.10/dbus/dbus-userdb-util.c),
[Unix lookup](https://gitlab.freedesktop.org/dbus/dbus/-/raw/dbus-1.14.10/dbus/dbus-sysdeps-unix.c),
[connection callback](https://gitlab.freedesktop.org/dbus/dbus/-/raw/dbus-1.14.10/bus/connection.c),
[policy](https://gitlab.freedesktop.org/dbus/dbus/-/raw/dbus-1.14.10/bus/policy.c).

Bus ID and machine ID are distinct: upstream Hello assigns a unique name; GetId
uses the bus context's generated UUID. Peer.GetMachineId follows a separate local
machine UUID path. A missing machine-id file is **not demonstrated** as a Hello/GetId
prerequisite. Sources: [bus context](https://gitlab.freedesktop.org/dbus/dbus/-/raw/dbus-1.14.10/bus/bus.c),
[driver](https://gitlab.freedesktop.org/dbus/dbus/-/raw/dbus-1.14.10/bus/driver.c).

## Bounded diagnostic design (subsequently executed below)

Use the same private bus, UID65534/cap0/NNP and isolation, without QQ, account data
or external sockets. Separate authentication from Hello/GetId. A same-connection
public protocol client must bound framing, signals, bytes and wall time; expose
only stage/booleans/timing, never GUID, unique name, body or raw stderr. Reuse a
public client where possible rather than introducing an unreviewed protocol parser.

The installed [dbus-send](https://dbus.freedesktop.org/doc/dbus-send.1.html) supports
fixed private-address calls, but separate invocations do not prove a single
connection's stages. `--bus` already performs Hello; explicitly sending another
Hello would be a faulty test. Reply timeouts also require an outer wall watchdog.

Only after baseline measurement should separately reviewed synthetic-identity or
machine-ID controls be considered. Do not bind host /etc, disable EXTERNAL, add
system services or relax the runtime's security/resource limits to force success.

## Subsequent baseline observation

The [no-QQ control](QQ_BUS_ADMISSION_CONTROL.md) and its
[immutable result](qq-bus-admission-evidence.json) now establish:

- A standalone raw EXTERNAL authentication connection returned the expected OK.
- Separate public-client invocations for explicit Hello and automatic-Hello/GetId
  both returned exit code 1, without timeout (all checks completed in 0.009 s).
- The client was UID/GID65534, cap-zero, NoNewPrivs. The offline gate passed;
  no QQ executable or native binding was invoked and software hashes were unchanged.

This separates authentication acceptance from successful bus-method use, but it
is **not same-connection stage proof**. Because method stdout/stderr were discarded,
exit code 1 alone does not discriminate admission, service or client errors. It
cannot yet prove that passwd/NSS explains either the bus failure or Electron stall.
The next discriminating check should retain only fixed client-error categories
(or strictly bounded protocol outcomes), before testing an isolated synthetic
identity control. No host identity files, system service or weaker authentication
were introduced in this baseline.
