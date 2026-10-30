"""Explicit offline slirp lifecycle: trusted driver owns outer netns and artifacts.

No host changes, arbitrary commands, DNS, TCP connects, or QQ operations. The
outer namespace remains fresh lo-DOWN/offline; inner OUTPUT is DROP before tap.
Children inherit the driver's process group so its deadline contains all work.
"""

import argparse
import errno
import ipaddress
import json
import os
from pathlib import Path
import select
import socket
import stat
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import egress_nft_posix_check as nft

baseline = nft.baseline
# Absolute system binaries and one fixed child protocol, never an execution API.
SLIRP = "/usr/bin/slirp4netns"
READY = b"DROP_READY\n"


def verified_slirp():
    """Require the fixed system slirp executable without searching PATH."""
    path = Path(SLIRP).resolve(strict=True)
    baseline.require(stat.S_ISREG(path.stat().st_mode) and os.access(path, os.X_OK),
                     "slirp_executable")
    return str(path)


def read_line(fd, deadline, limit=4096):
    """Read one bounded protocol line by absolute deadline, including EOF detection."""
    data = bytearray()
    while len(data) < limit:
        remaining = deadline - time.monotonic()
        baseline.require(remaining > 0 and select.select([fd], [], [], remaining)[0],
                         "pipe_timeout")
        part = os.read(fd, 1)
        if not part:
            baseline.require(not data, "pipe_partial_eof")
            return b""
        data.extend(part)
        if part == b"\n":
            return bytes(data)
    raise baseline.CheckFailure("pipe_output_limit")


def zero_counts(document):
    """Require exact four DROP identities and zero packet AND byte counts before attach."""
    counts = nft.counter_counts(document, require_positive=False)
    baseline.require(all(value == 0 for value in counts.values()), "nft_initial_counter")
    # Shared validator already proved the exact expression shape and integer types.
    baseline.require(all(item["rule"]["expr"][2]["counter"]["bytes"] == 0
                         for item in document["nftables"] if "rule" in item),
                     "nft_initial_counter")
    return counts


def verify_inner_network(parent):
    """Verify only lo/tap0, fixed slirp IPv4 configuration, and no routable IPv6."""
    baseline.namespace_guard(parent)
    baseline.require({name for _, name in socket.if_nameindex()} == {"lo", "tap0"},
                     "inner_links")
    executable = baseline.verified_ip()
    links, addresses, routes4, routes6 = [baseline.run_ip(executable, args)
                                        for args in baseline.READ_COMMANDS]
    baseline.require(type(links) is list and len(links) == 2
                     and {item.get("ifname") for item in links} == {"lo", "tap0"}, "inner_links")
    tap = next(item for item in links if item["ifname"] == "tap0")
    baseline.require("UP" in tap.get("flags", []), "inner_links")
    baseline.require(type(addresses) is list and len(addresses) == 2
                     and {item.get("ifname") for item in addresses} == {"lo", "tap0"},
                     "inner_addresses")
    found = []
    for interface in addresses:
        for address in interface.get("addr_info", []):
            value, prefix = address.get("local"), address.get("prefixlen")
            if interface["ifname"] == "lo":
                baseline.require((value, prefix) in (("127.0.0.1", 8), ("::1", 128)),
                                 "inner_addresses")
            elif address.get("family") == "inet":
                baseline.require((value, prefix) == ("10.0.2.100", 24), "inner_addresses")
                found.append(value)
            else:
                baseline.require(address.get("family") == "inet6" and prefix == 64
                                 and ipaddress.IPv6Address(value).is_link_local, "inner_addresses")
    baseline.require(found == ["10.0.2.100"], "inner_addresses")
    baseline.require(type(routes4) is list and len(routes4) <= 16, "inner_routes")
    defaults = 0
    for route in routes4:
        baseline.require(not any(key in route for key in ("via", "multipath", "nhid", "encap")),
                         "inner_routes")
        if route.get("dst") == "default":
            baseline.require(route.get("dev") == "tap0" and route.get("gateway") == "10.0.2.2"
                             and route.get("table", "main") in ("main", 254), "inner_routes")
            defaults += 1
        else:
            allowed = ({"10.0.2.0/24", "10.0.2.0", "10.0.2.100", "10.0.2.255"}
                       if route.get("dev") == "tap0" else
                       {"127.0.0.0/8", "127.0.0.0", "127.0.0.1", "127.255.255.255"}
                       if route.get("dev") == "lo" else set())
            baseline.require(route.get("dst") in allowed and "gateway" not in route,
                             "inner_routes")
    baseline.require(defaults == 1, "inner_routes")
    baseline.require(type(routes6) is list and len(routes6) <= 16, "inner_routes")
    for route in routes6:
        baseline.require(route.get("dev") in ("lo", "tap0")
                         and not any(key in route for key in ("gateway", "via", "multipath", "nhid", "encap")),
                         "inner_routes")
        destination = route.get("dst", "")
        baseline.require(destination in ("::1", "::1/128", "ff00::/8")
                         or ipaddress.IPv6Network(destination, strict=False).is_link_local,
                         "inner_routes")


def inspect_child(parent, executable):
    """Only after validating tap and unchanged DROP, attempt one kernel-blocked UDP."""
    verify_inner_network(parent)
    zero_counts(nft.nft_command(executable, apply=False))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sender:
        sender.settimeout(0.2)
        try:
            sender.sendto(b"offline-slirp", ("8.8.8.8", 9))
        except OSError as error:
            baseline.require(type(error.errno) is int and error.errno == errno.EPERM, "udp_errno")
        else:
            raise baseline.CheckFailure("udp_not_dropped")
    counts = nft.counter_counts(nft.nft_command(executable, apply=False), require_positive=False)
    baseline.require(counts["dsh_ipv4_udp"] > 0
                     and all(value == 0 for label, value in counts.items() if label != "dsh_ipv4_udp"),
                     "udp_counter")
    return {"kernel_drop_after_bridge": True, "udp_eperm": True, "rule_packets": counts}


def child(parent):
    """Guard and install DROP before READY; accept only inspect then bounded EOF."""
    deadline = time.monotonic() + 15
    baseline.initial_guard(parent)
    executable = nft.verified_nft()
    nft.nft_command(executable, apply=True)
    zero_counts(nft.nft_command(executable, apply=False))
    os.write(sys.stdout.fileno(), READY)
    request = read_line(sys.stdin.fileno(), deadline, 32)
    if not request:
        return 0  # Parent failed before attach; EOF safely retires the helper.
    baseline.require(request == b"inspect\n", "child_protocol")
    result = inspect_child(parent, executable)
    os.write(sys.stdout.fileno(), json.dumps(result, separators=(",", ":")).encode("ascii") + b"\n")
    baseline.require(read_line(sys.stdin.fileno(), deadline, 32) == b"", "child_protocol")
    return 0


def attach_guard(helper, outer, parent):
    """Confirm the literal owned PID is live in a third netns immediately before attach."""
    baseline.require(type(helper.pid) is int and helper.pid > 1 and helper.poll() is None,
                     "helper_pid")
    inner = os.readlink(f"/proc/{helper.pid}/ns/net")
    baseline.require(inner not in (outer, parent, os.readlink("/proc/1/ns/net")), "helper_namespace")


def retire(process):
    """Wait three seconds, kill only the owned direct child on timeout, and always reap."""
    if process is None:
        return False
    try:
        return process.wait(timeout=3) == 0
    except subprocess.TimeoutExpired:
        process.kill()  # Inherit driver's process group; never escape its global deadline.
        process.wait(timeout=3)
        return False  # Forced cleanup is never clean retirement evidence.


def lifecycle(parent, report):
    """Own all pipes/children; fail closed before launching and close exit writer first."""
    baseline.initial_guard(parent)
    report["offline_outer"] = True
    outer = os.readlink("/proc/self/ns/net")
    executable = verified_slirp()
    helper = bridge = None
    descriptors = []
    try:
        report["stage"] = "helper_ready"
        helper = subprocess.Popen(["/usr/bin/unshare", "--net", "/usr/bin/python3",
                                   str(Path(__file__).resolve()), "--child", "--parent-netns", outer],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, bufsize=0, close_fds=True)
        baseline.require(read_line(helper.stdout.fileno(), time.monotonic() + 3) == READY,
                         "helper_ready")
        report["kernel_drop_before_bridge"] = True
        # Repeat the outer offline gate after helper setup, before slirp exists.
        baseline.initial_guard(parent)
        attach_guard(helper, outer, parent)
        ready_read, ready_write = os.pipe()
        descriptors.extend((ready_read, ready_write))
        exit_read, exit_write = os.pipe()
        descriptors.extend((exit_read, exit_write))
        report["stage"] = "bridge_ready"
        bridge = subprocess.Popen([executable, "--configure", "--disable-host-loopback", "--disable-dns",
                                   "--enable-sandbox", "--enable-seccomp", f"--ready-fd={ready_write}",
                                   f"--exit-fd={exit_read}", str(helper.pid), "tap0"],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, close_fds=True,
                                  pass_fds=(ready_write, exit_read))
        report["bridge_started"] = True
        for fd in (ready_write, exit_read):
            os.close(fd)
            descriptors.remove(fd)
        baseline.require(select.select([ready_read], [], [], 5)[0], "bridge_ready_timeout")
        baseline.require(os.read(ready_read, 1) == b"1" and bridge.poll() is None, "bridge_ready")
        report["bridge_ready"] = True
        report["stage"] = "child_inspect"
        helper.stdin.write(b"inspect\n")
        result = json.loads(read_line(helper.stdout.fileno(), time.monotonic() + 3))
        baseline.require(type(result) is dict and result.get("kernel_drop_after_bridge") is True
                         and result.get("udp_eperm") is True, "child_report")
        report["kernel_drop_after_bridge"] = True
        report["udp_eperm"] = True
        report["rule_packets"] = result["rule_packets"]
        baseline.initial_guard(parent)
        baseline.require(bridge.poll() is None, "bridge_live")
    finally:
        # Only slirp inherits the exit read end; closing our sole writer signals EOF.
        for fd in descriptors:
            os.close(fd)
        try:
            report["slirp_retired"] = retire(bridge)
        finally:
            if helper is not None:
                helper.stdin.close()
                helper.stdout.close()
            report["helper_retired"] = retire(helper)
    baseline.require(report["slirp_retired"] and report["helper_retired"], "retirement")


def main(argv=None):
    """Expose only fixed parent/child modes and bounded static public lifecycle data."""
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--parent-netns", required=True)
    parser.add_argument("--child", action="store_true")
    args = parser.parse_args(argv)
    report = {"schema": 1, "offline_only": True, "status": "failed", "stage": "guard",
              "offline_outer": False, "bridge_started": False, "bridge_ready": False,
              "kernel_drop_before_bridge": False, "kernel_drop_after_bridge": False,
              "slirp_retired": False, "helper_retired": False}
    try:
        if args.child:
            return child(args.parent_netns)
        lifecycle(args.parent_netns, report)
        report.update(status="passed", stage="complete")
    except baseline.CheckFailure as error:
        report["stage"] = str(error)
    except Exception:
        # No raw native messages, command output, addresses, or host paths escape.
        report["error"] = "operation_failed"
    if not args.child:
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
