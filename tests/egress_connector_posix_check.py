"""Explicit offline Linux kernel check; never auto-launch unshare or run in pytest.

A trusted launcher supplies its parent netns identity. Namespace inequality is a
necessary guard, not proof of arbitrary namespace provenance or host isolation.
Only the private namespace's loopback receives the fixed public-looking literals.
"""

import argparse
from contextlib import ExitStack
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import struct
import subprocess
import sys
import threading
from unittest.mock import patch

# Direct script execution needs the repository import root, not an installed copy.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.qq_egress_connector import ConnectorError, connect_pinned
from tools.qq_egress_policy import DestinationPlan, validate_resolved_destination

# Test-only literals, not QQ destinations and never used outside guarded loopback.
V4 = "8.8.8.8"
V6 = "2001:4860:4860::8888"
TOKEN = b"offline-kernel-ping\n"
TIMEOUT = 2
READ_COMMANDS = (
    ("-j", "link", "show"),
    ("-j", "address", "show"),
    ("-j", "-4", "route", "show", "table", "all"),
    ("-j", "-6", "route", "show", "table", "all"),
)
WRITE_COMMANDS = (
    ("address", "add", V4 + "/32", "dev", "lo"),
    ("-6", "address", "add", V6 + "/128", "nodad", "dev", "lo"),
    ("link", "set", "dev", "lo", "up"),
)


class CheckFailure(Exception):
    """Carry only a static failure stage, never host data or raw error strings."""


def require(condition, stage):
    """Use explicit checks so Python optimization cannot remove safety guards."""
    if not condition:
        raise CheckFailure(stage)


def namespace_guard(parent):
    """Compare trusted launcher identity and visible init before all commands."""
    require(type(parent) is str and re.fullmatch(r"net:\[[0-9]+\]", parent), "parent_identity")
    current = os.readlink("/proc/self/ns/net")
    require(current != parent, "parent_namespace")
    require(current != os.readlink("/proc/1/ns/net"), "init_namespace")


def loopback_flags():
    """Read namespace-local flags via ioctl, not a potentially host-mounted sysfs."""
    import fcntl
    # This control socket has no bind/connect/send; ioctl only observes lo flags.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as control:
        response = fcntl.ioctl(control.fileno(), 0x8913, struct.pack("256s", b"lo"))
    return struct.unpack_from("H", response, 16)[0]


def pre_routes_safe(v4_text, v6_text):
    """Reject usable initial routes; allow only Linux IPv6 reject placeholders."""
    # Some kernels return exactly zero bytes for a fresh namespace's empty table.
    # Only that exact empty representation bypasses the header check; malformed
    # nonempty input, including whitespace-only text, must still fail closed.
    if v4_text != "":
        v4_lines = v4_text.splitlines()
        require(v4_lines and v4_lines[0].split()[:2] == ["Iface", "Destination"], "initial_routes")
        require(not any(line.strip() for line in v4_lines[1:]), "initial_routes")
    for line in v6_text.splitlines():
        fields = line.split()
        # Fresh Linux namespaces can expose unreachable ::/0 entries on lo.
        require(len(fields) == 10, "initial_routes")
        require(fields[0] == "0" * 32 and fields[1] == "00"
                and fields[2] == "0" * 32 and fields[3] == "00"
                and fields[4] == "0" * 32 and fields[9] == "lo", "initial_routes")
        require(int(fields[8], 16) & 0x200 != 0, "initial_routes")


def initial_guard(parent):
    """Fail before any subprocess on shared namespaces, links, UP or forwarding."""
    namespace_guard(parent)
    links = socket.if_nameindex()
    require(len(links) == 1 and links[0][1] == "lo", "initial_links")
    flags = loopback_flags()
    require(flags & 0x8 and not flags & 0x1, "initial_flags")
    pre_routes_safe(Path("/proc/net/route").read_text(encoding="ascii"),
                    Path("/proc/net/ipv6_route").read_text(encoding="ascii"))


def verified_ip():
    """Prefer the fixed system path before PATH lookup; require a real executable."""
    preferred = Path("/usr/sbin/ip")
    candidate = str(preferred) if preferred.exists() else shutil.which("ip")
    require(candidate is not None and os.path.isabs(candidate), "ip_executable")
    executable = Path(candidate).resolve(strict=True)
    require(stat.S_ISREG(executable.stat().st_mode) and os.access(executable, os.X_OK), "ip_executable")
    return str(executable)


def run_ip(executable, args):
    """Execute only fixed bounded argv with no shell or arbitrary command CLI."""
    require(type(args) is tuple and args in READ_COMMANDS + WRITE_COMMANDS, "command_allowlist")
    result = subprocess.run([executable, *args], check=False, timeout=TIMEOUT,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    require(result.returncode == 0, "ip_command")
    require(len(result.stdout) <= 65536 and len(result.stderr) <= 4096, "ip_output")
    return json.loads(result.stdout) if args in READ_COMMANDS else None


def check_routes(routes, version):
    """Allow exact local test hosts and kernel loopback routes, never forwarding."""
    require(type(routes) is list and len(routes) <= 16, "verified_routes")
    test = V4 if version == 4 else V6
    seen = 0
    for route in routes:
        require(type(route) is dict and route.get("dev") == "lo", "verified_routes")
        require(not any(key in route for key in ("gateway", "via", "multipath", "nhid", "encap")), "verified_routes")
        destination = route.get("dst", "")
        kind = route.get("type", "unicast")
        table = route.get("table", "main")
        protocol = route.get("protocol")
        # iproute2 may omit host prefix and IPv6 scope; IPv4 local scope is host.
        require(protocol == "kernel", "verified_routes")
        if destination in (test, test + ("/32" if version == 4 else "/128")):
            if kind == "local":
                require(table in ("local", 255), "verified_routes")
                require(route.get("scope", "host") == "host", "verified_routes")
                seen += 1
            else:
                # IPv6 address assignment also installs its exact /128 connected route.
                require(version == 6 and kind == "unicast" and table in ("main", 254), "verified_routes")
                require(route.get("scope", "link") == "link", "verified_routes")
        elif version == 4:
            permitted = {
                ("127.0.0.0/8", "local", "host"),
                ("127.0.0.0/8", "unicast", "link"),
                ("127.0.0.1", "local", "host"),
                ("127.0.0.0", "broadcast", "link"),
                ("127.255.255.255", "broadcast", "link"),
            }
            require((destination, kind, route.get("scope")) in permitted, "verified_routes")
            require(table in (("main", 254) if kind == "unicast" else ("local", 255)), "verified_routes")
        else:
            require(destination in ("::1", "::1/128") and kind in ("local", "unicast"), "verified_routes")
            require(table in (("main", 254) if kind == "unicast" else ("local", 255)), "verified_routes")
    require(seen == 1, "verified_routes")


def verify_configured(parent, executable, runner):
    """Recheck identity, only-lo links, exact addresses and routes before sockets."""
    namespace_guard(parent)
    require(len(socket.if_nameindex()) == 1 and socket.if_nameindex()[0][1] == "lo", "verified_links")
    links, addresses, v4, v6 = [runner(executable, args) for args in READ_COMMANDS]
    require(type(links) is list and len(links) == 1 and links[0].get("ifname") == "lo", "verified_links")
    require({"LOOPBACK", "UP"}.issubset(links[0].get("flags", [])), "verified_links")
    require(type(addresses) is list and len(addresses) == 1 and addresses[0].get("ifname") == "lo", "verified_addresses")
    found = set()
    for item in addresses[0].get("addr_info", []):
        value = (item.get("local"), item.get("prefixlen"))
        require(value in {(V4, 32), (V6, 128), ("127.0.0.1", 8), ("::1", 128)}, "verified_addresses")
        require(value not in found, "verified_addresses")
        require(not item.get("tentative", False) and not item.get("dadfailed", False), "verified_addresses")
        found.add(value)
    require({(V4, 32), (V6, 128)}.issubset(found), "verified_addresses")
    check_routes(v4, 4)
    check_routes(v6, 6)


def configure(parent, runner=run_ip):
    """All initial guards precede the first command; all postguards precede TCP."""
    initial_guard(parent)
    executable = verified_ip()
    for args in WRITE_COMMANDS:
        runner(executable, args)
    verify_configured(parent, executable, runner)


def receive_token(sock):
    """Read exactly the fixed small protocol length, handling TCP fragmentation."""
    data = b""
    while len(data) < len(TOKEN):
        part = sock.recv(len(TOKEN) - len(data))
        require(bool(part), "token_eof")
        data += part
    return data


def exchange(literal, family):
    """Perform one real kernel accept/connect/echo and verify socket ownership."""
    outcome = []
    owned = []
    listener = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
    owned.append(listener)
    worker = None
    try:
        listener.settimeout(TIMEOUT)
        listener.bind((literal, 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        def serve():
            """Accept once with bounded I/O and report only a boolean to the owner."""
            try:
                connection, _ = listener.accept()
                owned.append(connection)
                with connection:
                    connection.settimeout(TIMEOUT)
                    token = receive_token(connection)
                    require(token == TOKEN, "server_token")
                    connection.sendall(token)
                outcome.append(True)
            except Exception:
                outcome.append(False)

        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        plan = validate_resolved_destination(literal, port, [literal], {port})
        client = connect_pinned(plan, timeout_seconds=TIMEOUT, allowed_ports={port})
        owned.append(client)
        with client:
            require(client.family == family, "client_family")
            peer = client.getpeername()
            require(ipaddress.ip_address(peer[0]) == ipaddress.ip_address(literal) and peer[1] == port, "actual_peer")
            require(len(peer) == (2 if family == socket.AF_INET else 4), "actual_peer")
            if family == socket.AF_INET6:
                require(peer[2:] == (0, 0), "actual_peer")
            require(0 < client.gettimeout() <= TIMEOUT, "client_timeout")
            client.sendall(TOKEN)
            require(receive_token(client) == TOKEN, "echo_token")
        worker.join(TIMEOUT)
        require(not worker.is_alive() and outcome == [True], "server_result")
    except CheckFailure:
        raise
    except Exception:
        # IPv6 kernel failures remain failures with a public family-specific stage.
        raise CheckFailure("ipv4_exchange" if family == socket.AF_INET else "ipv6_exchange") from None
    finally:
        for sock in owned:
            sock.close()
        if worker is not None:
            worker.join(TIMEOUT)
        require(all(sock.fileno() == -1 for sock in owned), "owned_close")
    return len(owned)


def refusal():
    """Hold a non-listening bound port; prove one real failed allocation is closed."""
    created = []

    def factory(*args):
        """Track actual native sockets without faking connect or peer methods."""
        sock = socket.socket(*args)
        created.append(sock)
        return sock

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind((V4, 0))
        port = reservation.getsockname()[1]
        # A second permitted address exists, but the connector must never fall back.
        plan = validate_resolved_destination("offline.invalid", port, [V4, V6], {port})
        try:
            connect_pinned(plan, timeout_seconds=TIMEOUT, allowed_ports={port}, socket_factory=factory)
        except ConnectorError as error:
            require(error.code == "connect_failed" and str(error) == "connect_failed", "refusal_error")
        else:
            raise CheckFailure("refusal_missing")
        finally:
            closed = len(created) == 1 and created[0].fileno() == -1
            for sock in created:
                sock.close()
        require(closed, "refusal_close")
        require(0 < created[0].gettimeout() <= TIMEOUT, "refusal_timeout")
    require(reservation.fileno() == -1, "reservation_close")
    return 1


def policy_rejections():
    """Prove private literal and forged plan rejection without socket allocation."""
    allocations = []

    def forbidden_factory(*args):
        """Count a forbidden allocation attempt and fail without creating a socket."""
        allocations.append(1)
        raise CheckFailure("policy_allocation")

    with patch.object(socket, "socket", side_effect=forbidden_factory):
        try:
            validate_resolved_destination("127.0.0.1", 443, ["127.0.0.1"], {443})
        except ValueError:
            pass
        else:
            raise CheckFailure("private_policy")
        try:
            connect_pinned(DestinationPlan("offline.invalid", 443, ("127.0.0.1",)),
                           timeout_seconds=TIMEOUT, allowed_ports={443}, socket_factory=forbidden_factory)
        except ConnectorError as error:
            require(error.code == "invalid_plan", "forged_policy")
        else:
            raise CheckFailure("forged_policy")
    require(not allocations, "policy_allocation")
    return 2


def kernel_checks():
    """Spy on Python resolver helpers only; do not claim CPython C interception."""
    calls = []

    def forbidden_resolver(*args, **kwargs):
        """Record any forbidden Python helper entry without exposing its arguments."""
        calls.append(1)
        raise CheckFailure("python_resolver")

    with ExitStack() as stack:
        for name in ("getaddrinfo", "create_connection", "gethostbyname", "gethostbyname_ex"):
            stack.enter_context(patch.object(socket, name, side_effect=forbidden_resolver))
        closed = exchange(V4, socket.AF_INET)
        closed += exchange(V6, socket.AF_INET6)
        rejected = refusal()
        policy = policy_rejections()
    require(not calls, "python_resolver")
    return {"successful_families": 2, "echoes": 2, "owned_sockets_closed": closed,
            "refused_connections_closed": rejected, "preallocation_rejections": policy,
            "python_resolver_calls": len(calls)}


def main(argv=None):
    """Emit one bounded public JSON report; only the external driver writes files."""
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--parent-netns", required=True)
    args = parser.parse_args(argv)
    report = {"schema": 1, "offline_only": True, "status": "failed", "stage": "guard"}
    try:
        configure(args.parent_netns)
        report["stage"] = "kernel_checks"
        report["counts"] = kernel_checks()
        report.update(status="passed", stage="complete")
    except CheckFailure as error:
        report["stage"] = str(error)
    except Exception:
        # Native errors can contain paths or destination data; expose no raw text.
        pass
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
