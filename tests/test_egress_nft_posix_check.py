"""Fake-only nft guard/parser checks; never run commands or create real sockets."""

import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, Mock, patch

# Import without invoking the explicit integration entrypoint.
SPEC = importlib.util.spec_from_file_location(
    "egress_nft_posix_check", Path(__file__).with_name("egress_nft_posix_check.py"))
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


def document():
    """Model nft 1.x JSON output with exact metadata and explicit counter expressions."""
    entries = [{"metainfo": {"json_schema_version": 1}},
               {"table": {"family": "inet", "name": CHECK.TABLE}},
               {"chain": {"family": "inet", "table": CHECK.TABLE, "name": "output",
                          "type": "filter", "hook": "output", "prio": 0, "policy": "drop"}}]
    for label, family, protocol in CHECK.RULES:
        entries.append({"rule": {"family": "inet", "table": CHECK.TABLE, "chain": "output",
                                 "comment": label, "expr": [
                                     {"match": {"op": "==", "left": {"meta": {"key": "nfproto"}},
                                                "right": family}},
                                     {"match": {"op": "==", "left": {"meta": {"key": "l4proto"}},
                                                "right": protocol}},
                                     {"counter": {"packets": 1, "bytes": 60}}, {"drop": None}]}})
    return {"nftables": entries}


class NftGuardTests(unittest.TestCase):
    """Guard failure and invalid baselines must precede every nft operation."""

    def setUp(self):
        """Forbid all actual commands and sockets in this unit-only suite."""
        for target in (CHECK.subprocess, CHECK.socket):
            name = "run" if target is CHECK.subprocess else "socket"
            mocked = patch.object(target, name, side_effect=AssertionError("real I/O forbidden"))
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_namespace_failure_never_calls_nft(self):
        """The reused configure guard is mandatory before validation or application."""
        with patch.object(CHECK.baseline, "configure", side_effect=CHECK.baseline.CheckFailure("parent_namespace")), patch.object(CHECK.baseline, "kernel_checks") as kernel, patch.object(CHECK, "verified_nft") as verified, patch.object(CHECK, "nft_command") as nft, patch("builtins.print"):
            self.assertEqual(CHECK.main(["--parent-netns", "net:[100]"]), 1)
        kernel.assert_not_called()
        verified.assert_not_called()
        nft.assert_not_called()

    def test_baseline_failure_never_calls_nft(self):
        """Failed real-kernel infrastructure cannot be reported as firewall success."""
        with patch.object(CHECK.baseline, "configure"), patch.object(CHECK.baseline, "kernel_checks", side_effect=CHECK.baseline.CheckFailure("ipv6_exchange")), patch.object(CHECK, "nft_command") as nft, patch("builtins.print"):
            self.assertEqual(CHECK.main(["--parent-netns", "net:[100]"]), 1)
        nft.assert_not_called()

    def test_diagnostic_errno_remains_failed_and_redacted(self):
        """An OS error identifies the exact probe without exposing native strings."""
        cases = ((CHECK.errno.EPERM, "permission_denied", CHECK.errno.EPERM),
                 (5, "os_error", 5), (5000, "os_error", None), (True, "os_error", None))
        for number, kind, expected in cases:
            error = OSError(5, "secret-native-message", "secret-host-path")
            error.errno = number
            with patch.object(CHECK.baseline, "configure"), patch.object(CHECK.baseline, "kernel_checks", return_value={}), patch.object(CHECK, "verified_nft", return_value=CHECK.NFT), patch.object(CHECK, "nft_command"), patch.object(CHECK, "tcp_drop", return_value=1), patch.object(CHECK, "udp_probe", side_effect=[{}, {}, error]), patch("builtins.print") as output:
                self.assertEqual(CHECK.main(["--parent-netns", "net:[100]"]), 1)
            report = json.loads(output.call_args.args[0])
            self.assertEqual(report["stage"], "udp_v4")
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["error"], {"kind": kind, "errno": expected})
            self.assertNotIn("secret", output.call_args.args[0])

    def test_fixed_script(self):
        """The only mutation creates one exact drop-only output table and four rules."""
        self.assertEqual(CHECK.NFT, "/usr/sbin/nft")
        self.assertEqual(CHECK.RULES, (("dsh_ipv4_tcp", "ipv4", "tcp"),
                                      ("dsh_ipv6_tcp", "ipv6", "tcp"),
                                      ("dsh_ipv4_udp", "ipv4", "udp"),
                                      ("dsh_ipv6_udp", "ipv6", "udp")))
        expected = ["create table inet dsh_offline_test",
                    "add chain inet dsh_offline_test output { type filter hook output priority 0; policy drop; }"]
        expected += [f'add rule inet dsh_offline_test output meta nfproto {family} meta l4proto {protocol} counter drop comment "{label}"'
                     for label, family, protocol in CHECK.RULES]
        self.assertEqual(CHECK.SCRIPT.splitlines(), expected)

    def test_fixed_commands_and_timeout(self):
        """Apply/read commands have fixed argv, bounded timeout and captured output."""
        with patch.object(CHECK.subprocess, "run", return_value=Mock(returncode=0, stdout=b'{}', stderr=b'')) as run:
            self.assertIsNone(CHECK.nft_command(CHECK.NFT, apply=True))
            run.assert_called_once_with([CHECK.NFT, "-f", "-"], input=CHECK.SCRIPT.encode("ascii"),
                                        stdout=CHECK.subprocess.PIPE, stderr=CHECK.subprocess.PIPE,
                                        timeout=2, check=False)
            run.reset_mock()
            self.assertEqual(CHECK.nft_command(CHECK.NFT, apply=False), {})
            run.assert_called_once_with([CHECK.NFT, "-j", "list", "table", "inet", CHECK.TABLE],
                                        input=None, stdout=CHECK.subprocess.PIPE,
                                        stderr=CHECK.subprocess.PIPE, timeout=2, check=False)

    def test_command_failures_and_output_bounds(self):
        """Nonzero status, excess output and timeout never become successful evidence."""
        for result in (Mock(returncode=1, stdout=b'', stderr=b''),
                       Mock(returncode=0, stdout=b'x' * 65537, stderr=b''),
                       Mock(returncode=0, stdout=b'', stderr=b'x' * 4097)):
            with patch.object(CHECK.subprocess, "run", return_value=result):
                with self.assertRaises(CHECK.baseline.CheckFailure):
                    CHECK.nft_command(CHECK.NFT, apply=True)
        with patch.object(CHECK.subprocess, "run", side_effect=CHECK.subprocess.TimeoutExpired(CHECK.NFT, 2)):
            with self.assertRaises(CHECK.subprocess.TimeoutExpired):
                CHECK.nft_command(CHECK.NFT, apply=True)

    def test_cli_rejects_rules_override(self):
        """The public CLI has no alternate rules or executable surface."""
        with patch.object(CHECK.baseline, "configure") as configure, patch("sys.stderr"):
            with self.assertRaises(SystemExit):
                CHECK.main(["--parent-netns", "net:[100]", "--rules", "anything"])
        configure.assert_not_called()


class UdpTests(unittest.TestCase):
    """Mock UDP syscalls to enforce the narrow observed EPERM compatibility."""

    def probe(self, *, expect_drop, send_error=None, receive=False):
        """Supply context-managed fake sockets without any actual kernel operation."""
        receiver, sender = MagicMock(), MagicMock()
        for sock in (receiver, sender):
            sock.__enter__.return_value = sock
            sock.fileno.return_value = -1
        receiver.getsockname.return_value = (CHECK.baseline.V4, 12345)
        receiver.recvfrom.return_value = (b"offline-nft", (CHECK.baseline.V4, 23456))
        if not receive:
            receiver.recvfrom.side_effect = TimeoutError()
        sender.sendto.return_value = 11
        sender.sendto.side_effect = send_error
        with patch.object(CHECK.socket, "socket", side_effect=[receiver, sender]):
            return CHECK.udp_probe(CHECK.baseline.V4, CHECK.socket.AF_INET, expect_drop=expect_drop)

    def test_successful_baseline(self):
        """Before nft the same syscall path must actually deliver the expected token."""
        self.assertEqual(self.probe(expect_drop=False, receive=True), {"send": "sent", "received": True})

    def test_eperm_only_after_rules(self):
        """Only a post-rule EPERM can advance to receive-timeout and counter checks."""
        self.assertEqual(self.probe(expect_drop=True, send_error=OSError(CHECK.errno.EPERM, "hidden")),
                         {"send": "eperm", "received": False})
        with self.assertRaises(OSError):
            self.probe(expect_drop=False, send_error=OSError(CHECK.errno.EPERM, "hidden"))

    def test_other_errno_rejected(self):
        """EACCES and all unrelated errors cannot masquerade as observed nft drops."""
        for number in (CHECK.errno.EACCES, CHECK.errno.ENETUNREACH, CHECK.errno.EIO):
            with self.subTest(number=number), self.assertRaises(OSError):
                self.probe(expect_drop=True, send_error=OSError(number, "hidden"))

    def test_received_packet_or_baseline_timeout_rejected(self):
        """Drop tests reject delivery even after EPERM; baseline cannot time out."""
        with self.assertRaises(CHECK.baseline.CheckFailure):
            self.probe(expect_drop=True, send_error=OSError(CHECK.errno.EPERM, "hidden"), receive=True)
        with self.assertRaises(CHECK.baseline.CheckFailure):
            self.probe(expect_drop=False)

    def test_eperm_without_counter_never_passes(self):
        """Even complete negative probes cannot pass without all four positive counters."""
        data = document()
        data["nftables"][5]["rule"]["expr"][2]["counter"]["packets"] = 0
        with patch.object(CHECK.baseline, "configure"), patch.object(CHECK.baseline, "kernel_checks", return_value={}), patch.object(CHECK, "verified_nft", return_value=CHECK.NFT), patch.object(CHECK, "nft_command", side_effect=[None, data]), patch.object(CHECK, "tcp_drop", return_value=1), patch.object(CHECK, "udp_probe", return_value={"send": "eperm", "received": False}), patch("builtins.print") as output:
            self.assertEqual(CHECK.main(["--parent-netns", "net:[100]"]), 1)
        report = json.loads(output.call_args.args[0])
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["stage"], "nft_counter")


class CounterTests(unittest.TestCase):
    """Counters alone are insufficient: require their exact rule and chain identity."""

    def test_valid_document(self):
        """All four enum counters must be present and nonzero."""
        self.assertEqual(CHECK.counter_counts(document()), {label: 1 for label, _, _ in CHECK.RULES})

    def test_missing_or_zero_counter(self):
        """Absent, untyped, zero and boolean counters never establish packet drops."""
        for counter in ({}, {"packets": 0, "bytes": 60}, {"packets": True, "bytes": 60},
                        {"packets": 1, "bytes": 0}, {"packets": "1", "bytes": 60}):
            data = document()
            data["nftables"][3]["rule"]["expr"][2] = {"counter": counter}
            with self.subTest(counter=counter), self.assertRaises(CHECK.baseline.CheckFailure):
                CHECK.counter_counts(data)

    def test_wrong_rule_identity(self):
        """Wrong table, family, chain, labels, duplicate labels or protocol fail closed."""
        for key, value in (("family", "ip"), ("table", "other"), ("chain", "input"),
                           ("comment", "other"), ("comment", "dsh_ipv6_tcp")):
            data = document()
            data["nftables"][3]["rule"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(CHECK.baseline.CheckFailure):
                CHECK.counter_counts(data)
        data = document()
        data["nftables"][3]["rule"]["expr"][1]["match"]["right"] = "udp"
        with self.assertRaises(CHECK.baseline.CheckFailure):
            CHECK.counter_counts(data)

    def test_missing_extra_and_malformed_shape(self):
        """Partial, extra, malformed and accept-verdict rules are not equivalent evidence."""
        complete = document()
        missing = copy.deepcopy(complete)
        missing["nftables"].pop()
        extra = copy.deepcopy(complete)
        extra["nftables"].append(copy.deepcopy(extra["nftables"][-1]))
        accept = copy.deepcopy(complete)
        accept["nftables"][3]["rule"]["expr"][3] = {"accept": None}
        chain = copy.deepcopy(complete)
        chain["nftables"][2]["chain"]["policy"] = "accept"
        for data in (None, {}, {"nftables": {}}, {"nftables": [None]}, missing, extra, accept, chain):
            with self.subTest(data=data), self.assertRaises(CHECK.baseline.CheckFailure):
                CHECK.counter_counts(data)


if __name__ == "__main__":
    unittest.main()
