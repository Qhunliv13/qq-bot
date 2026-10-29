"""Explicit namespace-only nft drop test; no launcher, host firewall or QQ traffic.

The trusted external driver owns namespace creation/destruction and artifacts.
Every invocation requires a fresh namespace accepted by the existing fixture.
"""

import argparse
import errno
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys

# Reuse the existing isolation checks verbatim, including direct script execution.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import egress_connector_posix_check as baseline

# No PATH lookup, executable override, rules input, forwarding, or arbitrary CLI.
NFT = "/usr/sbin/nft"
TABLE = "dsh_offline_test"
RULES = (
    ("dsh_ipv4_tcp", "ipv4", "tcp"),
    ("dsh_ipv6_tcp", "ipv6", "tcp"),
    ("dsh_ipv4_udp", "ipv4", "udp"),
    ("dsh_ipv6_udp", "ipv6", "udp"),
)
SCRIPT = """create table inet dsh_offline_test
add chain inet dsh_offline_test output { type filter hook output priority 0; policy drop; }
add rule inet dsh_offline_test output meta nfproto ipv4 meta l4proto tcp counter drop comment "dsh_ipv4_tcp"
add rule inet dsh_offline_test output meta nfproto ipv6 meta l4proto tcp counter drop comment "dsh_ipv6_tcp"
add rule inet dsh_offline_test output meta nfproto ipv4 meta l4proto udp counter drop comment "dsh_ipv4_udp"
add rule inet dsh_offline_test output meta nfproto ipv6 meta l4proto udp counter drop comment "dsh_ipv6_udp"
"""


def verified_nft():
    """Resolve only the fixed absolute system binary and require executable regularity."""
    candidate = Path(NFT)
    baseline.require(candidate.is_absolute(), "nft_executable")
    resolved = candidate.resolve(strict=True)
    baseline.require(resolved.is_absolute() and stat.S_ISREG(resolved.stat().st_mode)
                     and os.access(resolved, os.X_OK), "nft_executable")
    return str(resolved)


def nft_command(executable, *, apply):
    """Run one of two fixed bounded commands; never accept user-provided nft syntax."""
    args = ["-f", "-"] if apply else ["-j", "list", "table", "inet", TABLE]
    result = subprocess.run([executable, *args], input=SCRIPT.encode("ascii") if apply else None,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=2, check=False)
    baseline.require(result.returncode == 0, "nft_command")
    baseline.require(len(result.stdout) <= 65536 and len(result.stderr) <= 4096, "nft_output")
    return None if apply else json.loads(result.stdout)


def counter_counts(document):
    """Require the exact table, output hook, four identities and positive counters."""
    baseline.require(type(document) is dict and type(document.get("nftables")) is list,
                     "nft_json")
    tables, chains, rules = [], [], []
    for item in document["nftables"]:
        baseline.require(type(item) is dict and len(item) == 1, "nft_json")
        kind = next(iter(item))
        baseline.require(kind in ("metainfo", "table", "chain", "rule"), "nft_json")
        if kind == "metainfo":
            continue  # nft's version metadata is not emitted into the public report.
        value = item[kind]
        baseline.require(type(value) is dict and value.get("family") == "inet", "nft_identity")
        if kind == "table":
            baseline.require(value.get("name") == TABLE, "nft_identity")
            tables.append(value)
        else:
            baseline.require(value.get("table") == TABLE, "nft_identity")
            (chains if kind == "chain" else rules).append(value)
    baseline.require(len(tables) == 1 and len(chains) == 1 and len(rules) == 4, "nft_identity")
    chain = chains[0]
    baseline.require(chain.get("name") == "output" and chain.get("type") == "filter"
                     and chain.get("hook") == "output" and type(chain.get("prio")) is int
                     and chain["prio"] == 0 and chain.get("policy") == "drop", "nft_chain")
    expected = {label: (family, protocol) for label, family, protocol in RULES}
    counts = {}
    for rule in rules:
        label = rule.get("comment")
        baseline.require(type(label) is str and label in expected and label not in counts
                         and rule.get("chain") == "output", "nft_identity")
        family, protocol = expected[label]
        expressions = rule.get("expr")
        baseline.require(type(expressions) is list and len(expressions) == 4, "nft_rule")
        for expression, key, value in zip(expressions[:2], ("nfproto", "l4proto"), (family, protocol)):
            baseline.require(expression == {"match": {"op": "==", "left": {"meta": {"key": key}},
                                                       "right": value}}, "nft_rule")
        baseline.require(type(expressions[2]) is dict and set(expressions[2]) == {"counter"}
                         and expressions[3] == {"drop": None}, "nft_rule")
        counter = expressions[2]["counter"]
        baseline.require(type(counter) is dict and set(counter) == {"packets", "bytes"}
                         and type(counter["packets"]) is int and counter["packets"] >= 1
                         and type(counter["bytes"]) is int and counter["bytes"] >= 1, "nft_counter")
        counts[label] = counter["packets"]
    return counts


def tcp_drop(literal, family):
    """A live listener must become unreachable, with one real allocation closed."""
    created = []

    def factory(*args):
        """Track real sockets, without mocking the kernel connect or its deadline."""
        sock = socket.socket(*args)
        created.append(sock)
        return sock

    with socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP) as listener:
        listener.settimeout(0.3)
        listener.bind((literal, 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        plan = baseline.validate_resolved_destination(literal, port, [literal], {port})
        try:
            client = baseline.connect_pinned(plan, timeout_seconds=1, allowed_ports={port},
                                             socket_factory=factory)
        except baseline.ConnectorError as error:
            baseline.require(error.code == "deadline_exceeded" and str(error) == "deadline_exceeded",
                             "tcp_error")
        else:
            client.close()
            raise baseline.CheckFailure("tcp_not_dropped")
        finally:
            # Observe cleanup before our defensive close, so leaks cannot be hidden.
            closed = len(created) == 1 and created[0].fileno() == -1
            for sock in created:
                sock.close()
        baseline.require(closed, "tcp_allocation_close")
        try:
            accepted, _ = listener.accept()
        except TimeoutError:
            pass  # A live listener must have received no connection despite the probe.
        else:
            accepted.close()
            raise baseline.CheckFailure("tcp_accepted")
    baseline.require(listener.fileno() == -1, "tcp_listener_close")
    return 1


def udp_probe(literal, family, *, expect_drop):
    """Prove baseline delivery or timeout; drop EPERM still needs exact nft counters."""
    outcome = "sent"
    received = False
    with socket.socket(family, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as receiver:
        with socket.socket(family, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sender:
            receiver.settimeout(0.2)
            sender.settimeout(0.2)
            receiver.bind((literal, 0))
            port = receiver.getsockname()[1]
            try:
                baseline.require(sender.sendto(b"offline-nft", (literal, port)) == 11, "udp_send")
            except OSError as error:
                # Linux local OUTPUT drop can synchronously return EPERM. Accept
                # only that observed errno after rules; baseline and EACCES fail.
                if not (expect_drop and type(error.errno) is int and error.errno == errno.EPERM):
                    raise
                outcome = "eperm"
            try:
                token, _ = receiver.recvfrom(32)
            except TimeoutError:
                baseline.require(expect_drop, "udp_baseline_timeout")
            else:
                baseline.require(not expect_drop, "udp_not_dropped")
                baseline.require(token == b"offline-nft", "udp_baseline_token")
                received = True
    baseline.require(receiver.fileno() == -1 and sender.fileno() == -1, "udp_close")
    return {"send": outcome, "received": received}


def main(argv=None):
    """Guard, prove baseline, apply fixed rules, then emit only bounded public data."""
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--parent-netns", required=True)
    args = parser.parse_args(argv)
    report = {"schema": 1, "offline_only": True, "status": "failed", "stage": "guard"}
    try:
        baseline.configure(args.parent_netns)
        report["stage"] = "baseline"
        report["baseline"] = baseline.kernel_checks()
        for literal, family, suffix in ((baseline.V4, socket.AF_INET, "v4"),
                                        (baseline.V6, socket.AF_INET6, "v6")):
            report["stage"] = "udp_baseline_" + suffix
            udp_probe(literal, family, expect_drop=False)
        report["baseline"]["udp_successful_families"] = 2
        report["stage"] = "nft_apply"
        executable = verified_nft()
        nft_command(executable, apply=True)
        tcp = 0
        udp_outcomes = {}
        for literal, family, suffix in ((baseline.V4, socket.AF_INET, "v4"),
                                        (baseline.V6, socket.AF_INET6, "v6")):
            report["stage"] = "tcp_" + suffix
            tcp += tcp_drop(literal, family)
            report["stage"] = "udp_" + suffix
            udp_outcomes[suffix] = udp_probe(literal, family, expect_drop=True)
        report["stage"] = "nft_counters"
        counts = counter_counts(nft_command(executable, apply=False))
        report["counts"] = {"tcp_dropped": tcp, "udp_dropped": len(udp_outcomes),
                            "failed_connector_allocations_closed": tcp, "rule_packets": counts,
                            "udp_send_outcomes": udp_outcomes}
        report.update(status="passed", stage="complete")
    except baseline.CheckFailure as error:
        report["stage"] = str(error)
    except OSError as error:
        # Fixed categories and bounded errno expose diagnosis, never native messages,
        # exception class names, filenames, addresses or other host-derived strings.
        number = error.errno
        report["error"] = {
            "kind": "permission_denied" if type(number) is int and number in (errno.EPERM, errno.EACCES) else "os_error",
            "errno": number if type(number) is int and 0 <= number <= 4095 else None,
        }
    except Exception:
        report["error"] = {"kind": "unexpected_error"}
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
