"""Explicit isolated-loopback selective compiler check; no launcher or QQ traffic.

Trusted external owner creates/destroys a fresh network namespace. UDP proves
positive exact-target delivery; TCP proves denial only (no TCP positive claim).
Never invoke automatically or apply this script to a host firewall.
"""

import argparse
from contextlib import ExitStack
import errno
import json
from pathlib import Path
import socket
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import egress_nft_posix_check as nft

from tools.qq_egress_policy import DestinationPlan
from tools.qq_egress_rules import DROP_COMMENT, TABLE, build_rules

baseline = nft.baseline
TOKEN = b"offline-selective"


def command(executable, source=None):
    """Apply compiler source or inspect only its fixed table, within the guard."""
    args = ["-f", "-"] if source is not None else ["-j", "list", "table", "inet", TABLE]
    result = subprocess.run([executable, *args], input=source.encode("ascii") if source is not None else None,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=2, check=False)
    baseline.require(result.returncode == 0, "nft_command")
    baseline.require(len(result.stdout) <= 65536 and len(result.stderr) <= 4096, "nft_output")
    return None if source is not None else json.loads(result.stdout)


def probe(receiver, literal, *, denied):
    """Use a live fixed-port receiver and exact token; EPERM alone proves nothing."""
    with socket.socket(receiver.family, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sender:
        sender.settimeout(0.2)
        try:
            baseline.require(sender.sendto(TOKEN, (literal, receiver.getsockname()[1])) == len(TOKEN), "udp_send")
        except OSError as error:
            baseline.require(denied and type(error.errno) is int and error.errno == errno.EPERM, "udp_errno")
        try:
            data, _ = receiver.recvfrom(64)
        except TimeoutError:
            baseline.require(denied, "udp_positive_timeout")
        else:
            baseline.require(not denied and data == TOKEN, "udp_delivery")


def counters(document, endpoints):
    """Check exact nft JSON matches and counters, not just comments or return codes."""
    baseline.require(type(document) is dict and type(document.get("nftables")) is list, "nft_json")
    tables, chains, rules = [], [], []
    for item in document["nftables"]:
        baseline.require(type(item) is dict and len(item) == 1, "nft_json")
        kind = next(iter(item))
        baseline.require(kind in ("metainfo", "table", "chain", "rule"), "nft_json")
        if kind == "metainfo":
            continue
        value = item[kind]
        baseline.require(type(value) is dict and value.get("family") == "inet", "nft_identity")
        baseline.require(value.get("name" if kind == "table" else "table") == TABLE, "nft_identity")
        {"table": tables, "chain": chains, "rule": rules}[kind].append(value)
    baseline.require(len(tables) == 1 and len(chains) == 1 and len(rules) == 5, "nft_identity")
    chain = chains[0]
    baseline.require(chain.get("name") == "output" and chain.get("hook") == "output"
                     and chain.get("type") == "filter" and type(chain.get("prio")) is int
                     and chain["prio"] == 0 and chain.get("policy") == "drop", "nft_chain")
    expected = {}
    for index, (version, literal, port) in enumerate(endpoints):
        for protocol in ("tcp", "udp"):
            expected[f"qq_egress_v{version}_{protocol}_{index}"] = (version, literal, port, protocol)
    counts = {}
    for rule in rules:
        label, expressions = rule.get("comment"), rule.get("expr")
        baseline.require(type(label) is str and label not in counts and rule.get("chain") == "output", "nft_identity")
        baseline.require(type(expressions) is list, "nft_rule")
        if label == DROP_COMMENT:
            baseline.require(len(expressions) == 2 and expressions[-1] == {"drop": None}, "nft_rule")
            minimum = 4
        else:
            baseline.require(label in expected and len(expressions) == 4 and expressions[-1] == {"accept": None}, "nft_rule")
            version, literal, port, protocol = expected[label]
            for expression, payload, value in (
                (expressions[0], {"protocol": "ip" if version == 4 else "ip6", "field": "daddr"}, literal),
                (expressions[1], {"protocol": protocol, "field": "dport"}, port),
            ):
                baseline.require(expression == {"match": {"op": "==", "left": {"payload": payload}, "right": value}}, "nft_rule")
            minimum = 1 if protocol == "udp" else 0
        counter_expr = expressions[-2]
        baseline.require(type(counter_expr) is dict and set(counter_expr) == {"counter"}, "nft_counter")
        value = counter_expr["counter"]
        baseline.require(type(value) is dict and set(value) == {"packets", "bytes"}
                         and all(type(value[key]) is int and value[key] >= minimum for key in ("packets", "bytes")), "nft_counter")
        if label != DROP_COMMENT and expected[label][3] == "tcp":
            baseline.require(value["packets"] == 0 and value["bytes"] == 0, "tcp_accept_counter")
        counts[label] = value["packets"]
    baseline.require(set(counts) == set(expected) | {DROP_COMMENT}, "nft_identity")
    return counts


def checks():
    """After namespace guard, prove baseline, reject forgeries, then apply compiler."""
    for literal, family in ((baseline.V4, socket.AF_INET), (baseline.V6, socket.AF_INET6)):
        nft.udp_probe(literal, family, expect_drop=False)
    with ExitStack() as stack:
        receivers, endpoints, plans = [], [], []
        for version, literal, family in ((4, baseline.V4, socket.AF_INET), (6, baseline.V6, socket.AF_INET6)):
            pair = []
            for _ in range(2):
                receiver = stack.enter_context(socket.socket(family, socket.SOCK_DGRAM, socket.IPPROTO_UDP))
                receiver.settimeout(0.2)
                receiver.bind((literal, 0))
                pair.append(receiver)
            receivers.append((literal, pair))
            port = pair[0].getsockname()[1]
            endpoints.append((version, literal, port))
            plans.append(DestinationPlan(literal, port, (literal,)))
        private = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP))
        private.settimeout(0.2)
        private.bind(("127.0.0.1", 0))
        ports = {plan.port for plan in plans}
        # Reject complete inputs before any apply; fixture ownership intentionally
        # distinguishes synthetic loopback test destinations from host addresses.
        for forged, blocked in (([DestinationPlan("offline.invalid", plans[0].port, ("127.0.0.1",))], set()),
                                (plans, {baseline.V4})):
            try:
                build_rules(forged, allowed_ports=ports, blocked_addresses=blocked, protocols={"tcp", "udp"})
            except ValueError:
                pass
            else:
                raise baseline.CheckFailure("compiler_rejection")
        source = build_rules(plans, allowed_ports=ports, blocked_addresses={"127.0.0.1", "::1"}, protocols={"tcp", "udp"})
        executable = nft.verified_nft()
        command(executable, source)
        for literal, pair in receivers:
            probe(pair[0], literal, denied=False)
            probe(pair[1], literal, denied=True)
        probe(private, "127.0.0.1", denied=True)
        # Reuse existing connector denial/cleanup proof; reserve its listener on
        # a port distinct from all compiler-authorized ports via TCP reservations.
        for family in (socket.AF_INET, socket.AF_INET6):
            for port in ports:
                sock = stack.enter_context(socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP))
                if family == socket.AF_INET6:
                    # Avoid dual-stack wildcard port ownership in the fixture.
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                sock.bind((baseline.V4 if family == socket.AF_INET else baseline.V6, port))
        tcp = nft.tcp_drop(baseline.V4, socket.AF_INET)
        counts = counters(command(executable), endpoints)
    return {"udp_allowed": 2, "udp_denied": 3, "tcp_denied": tcp, "tcp_positive_proven": False,
            "compiler_rejections": 2, "rule_packets": counts}


def main(argv=None):
    """Fail closed before compilation/apply unless existing namespace guard passes."""
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--parent-netns", required=True)
    args = parser.parse_args(argv)
    report = {"schema": 1, "offline_only": True, "status": "failed", "stage": "guard"}
    try:
        baseline.configure(args.parent_netns)
        report["stage"] = "selective_checks"
        report["counts"] = checks()
        report.update(status="passed", stage="complete")
    except baseline.CheckFailure as error:
        report["stage"] = str(error)
    except Exception:
        report["error"] = {"kind": "unexpected_error"}
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
