"""Fake-only slirp lifecycle tests; never create namespaces or send real packets."""

import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, Mock, patch

from test_egress_nft_posix_check import document

SPEC = importlib.util.spec_from_file_location(
    "egress_slirp_posix_check", Path(__file__).with_name("egress_slirp_posix_check.py"))
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


def zeros():
    """Reuse the existing exact rule schema with genuinely zero packet/byte counters."""
    data = document()
    for item in data["nftables"]:
        if "rule" in item:
            item["rule"]["expr"][2]["counter"] = {"packets": 0, "bytes": 0}
    return data


class CounterModeTests(unittest.TestCase):
    """Nonnegative mode never weakens default positive-counter evidence."""

    def test_zero_requires_explicit_mode(self):
        """The old no-argument validator still rejects zero counters."""
        with self.assertRaises(CHECK.baseline.CheckFailure):
            CHECK.nft.counter_counts(zeros())
        self.assertEqual(CHECK.zero_counts(zeros()), {label: 0 for label, _, _ in CHECK.nft.RULES})

    def test_strict_boolean_mode(self):
        """Truthy/falsy integers, strings and None cannot choose a weaker mode."""
        for value in (0, 1, None, "", "false", [], {}):
            with self.subTest(value=value), self.assertRaises(CHECK.baseline.CheckFailure):
                CHECK.nft.counter_counts(zeros(), require_positive=value)

    def test_bad_nonnegative_and_nonzero_gate(self):
        """Reject negative/bool counters and both nonzero packets and nonzero bytes."""
        for key in ("packets", "bytes"):
            for value in (-1, True, "0", 1):
                data = zeros()
                data["nftables"][3]["rule"]["expr"][2]["counter"][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(CHECK.baseline.CheckFailure):
                    CHECK.zero_counts(data)

    def test_zero_mode_still_requires_drop_structure(self):
        """Accept policy or verdict, wrong identity, or missing rules never pass."""
        for change in ("policy", "verdict", "identity", "missing"):
            data = zeros()
            if change == "policy":
                data["nftables"][2]["chain"]["policy"] = "accept"
            elif change == "verdict":
                data["nftables"][3]["rule"]["expr"][3] = {"accept": None}
            elif change == "identity":
                data["nftables"][3]["rule"]["comment"] = "other"
            else:
                data["nftables"].pop()
            with self.subTest(change=change), self.assertRaises(CHECK.baseline.CheckFailure):
                CHECK.zero_counts(data)


class LifecycleTests(unittest.TestCase):
    """All launch and cleanup branches use fakes, including failed readiness."""

    def setUp(self):
        """Forbid accidental subprocesses, sockets, pipes, and filesystem observations."""
        targets = ((CHECK.subprocess, "Popen"), (CHECK.subprocess, "run"),
                   (CHECK.socket, "socket"), (CHECK.os, "pipe"), (CHECK.os, "readlink"))
        for target, name in targets:
            mocked = patch.object(target, name, side_effect=AssertionError("real I/O forbidden"))
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_outer_guard_prelaunch(self):
        """Unsafe outer state prevents even executable lookup or helper creation."""
        with patch.object(CHECK.baseline, "initial_guard", side_effect=CHECK.baseline.CheckFailure("initial_links")), patch.object(CHECK, "verified_slirp") as verify, patch.object(CHECK.subprocess, "Popen") as launch, patch("builtins.print") as output:
            self.assertEqual(CHECK.main(["--parent-netns", "net:[100]"]), 1)
        verify.assert_not_called()
        launch.assert_not_called()
        self.assertFalse(json.loads(output.call_args.args[0])["bridge_started"])

    def test_child_guard_pre_nft(self):
        """Child's own initial guard precedes any nft lookup, apply, or READY."""
        with patch.object(CHECK.baseline, "initial_guard", side_effect=CHECK.baseline.CheckFailure("parent_namespace")), patch.object(CHECK.nft, "verified_nft") as verify, patch.object(CHECK.os, "write") as write:
            self.assertEqual(CHECK.main(["--child", "--parent-netns", "net:[100]"]), 1)
        verify.assert_not_called()
        write.assert_not_called()

    def test_child_zero_gate_pre_ready(self):
        """A nonzero initial counter forbids READY even after successful apply."""
        with patch.object(CHECK.baseline, "initial_guard"), patch.object(CHECK.nft, "verified_nft", return_value="/usr/sbin/nft"), patch.object(CHECK.nft, "nft_command", side_effect=[None, document()]), patch.object(CHECK.os, "write") as write:
            self.assertEqual(CHECK.main(["--child", "--parent-netns", "net:[100]"]), 1)
        write.assert_not_called()

    def test_attach_rejects_parent_outer_and_dead_pid(self):
        """Literal owned helper PID must identify a third live namespace."""
        helper = Mock(pid=42)
        helper.poll.return_value = None
        for identity in ("net:[100]", "net:[200]", "net:[1]"):
            with patch.object(CHECK.os, "readlink", side_effect=[identity, "net:[1]"]):
                with self.assertRaises(CHECK.baseline.CheckFailure):
                    CHECK.attach_guard(helper, "net:[200]", "net:[100]")
        helper.poll.return_value = 1
        with self.assertRaises(CHECK.baseline.CheckFailure):
            CHECK.attach_guard(helper, "net:[200]", "net:[100]")

    def test_retire_timeout_kills_and_reaps_only_child(self):
        """Forced cleanup returns false even when the subsequent reap succeeds."""
        process = Mock()
        process.wait.side_effect = [CHECK.subprocess.TimeoutExpired("fixed", 3), 0]
        self.assertFalse(CHECK.retire(process))
        process.kill.assert_called_once_with()
        self.assertEqual([call.kwargs for call in process.wait.call_args_list], [{"timeout": 3}, {"timeout": 3}])

    def exercise_lifecycle(self, *, timeout=False, bad_namespace=False):
        """Run a fake lifecycle and retain launch/close evidence for exact assertions."""
        helper, bridge = MagicMock(pid=42), MagicMock(pid=43)
        helper.poll.return_value = bridge.poll.return_value = None
        helper.wait.return_value = bridge.wait.return_value = 0
        result = {"kernel_drop_after_bridge": True, "udp_eperm": True,
                  "rule_packets": {"dsh_ipv4_udp": 1}}
        report = {}
        with patch.object(CHECK.baseline, "initial_guard") as guard, patch.object(CHECK.os, "readlink", return_value="net:[200]"), patch.object(CHECK, "verified_slirp", return_value=CHECK.SLIRP), patch.object(CHECK.subprocess, "Popen", side_effect=[helper, bridge]) as launch, patch.object(CHECK, "read_line", side_effect=[CHECK.READY, json.dumps(result).encode()]), patch.object(CHECK, "attach_guard", side_effect=CHECK.baseline.CheckFailure("helper_namespace") if bad_namespace else None), patch.object(CHECK.os, "pipe", side_effect=[(10, 11), (12, 13)]), patch.object(CHECK.os, "close") as close, patch.object(CHECK.select, "select", return_value=([] if timeout else [10], [], [])), patch.object(CHECK.os, "read", return_value=b"1"):
            if timeout or bad_namespace:
                with self.assertRaises(CHECK.baseline.CheckFailure):
                    CHECK.lifecycle("net:[100]", report)
            else:
                CHECK.lifecycle("net:[100]", report)
        return report, helper, bridge, launch, close, guard

    def test_ready_timeout_cleans_owned_children_and_pipes(self):
        """Missing ready byte closes every fd and retires both owned children."""
        report, helper, bridge, launch, close, _ = self.exercise_lifecycle(timeout=True)
        self.assertEqual(launch.call_count, 2)
        self.assertTrue(report["slirp_retired"] and report["helper_retired"])
        self.assertEqual({call.args[0] for call in close.call_args_list}, {10, 11, 12, 13})
        helper.stdin.close.assert_called_once_with()
        bridge.wait.assert_called_once_with(timeout=3)
        helper.stdin.write.assert_not_called()

    def test_namespace_guard_blocks_bridge_launch(self):
        """An invalid helper namespace cannot trigger slirp or pipe allocation."""
        _, helper, _, launch, close, _ = self.exercise_lifecycle(bad_namespace=True)
        self.assertEqual(launch.call_count, 1)
        close.assert_not_called()
        helper.wait.assert_called_once_with(timeout=3)

    def test_fixed_success_path_and_fd_ownership(self):
        """Only fixed CLI, inherited group, and the two intended descriptors reach slirp."""
        report, helper, _, launch, close, guard = self.exercise_lifecycle()
        command = launch.call_args_list[1].args[0]
        self.assertEqual(command, [CHECK.SLIRP, "--configure", "--disable-host-loopback", "--disable-dns",
                                   "--enable-sandbox", "--enable-seccomp", "--ready-fd=11", "--exit-fd=12", "42", "tap0"])
        self.assertEqual(launch.call_args_list[1].kwargs["pass_fds"], (11, 12))
        for call in launch.call_args_list:
            self.assertNotIn("start_new_session", call.kwargs)
            self.assertNotIn("shell", call.kwargs)
        self.assertEqual(guard.call_count, 3)
        self.assertEqual([call.args[0] for call in close.call_args_list], [11, 12, 10, 13])
        self.assertTrue(report["kernel_drop_after_bridge"])
        helper.stdin.write.assert_called_once_with(b"inspect\n")

    def test_read_timeout_and_output_bound(self):
        """Select deadline and byte cap fail rather than hanging or retaining raw output."""
        with patch.object(CHECK.select, "select", return_value=([], [], [])):
            with self.assertRaises(CHECK.baseline.CheckFailure):
                CHECK.read_line(123, CHECK.time.monotonic() + 1)
        with patch.object(CHECK.select, "select", return_value=([123], [], [])), patch.object(CHECK.os, "read", return_value=b"x"):
            with self.assertRaises(CHECK.baseline.CheckFailure):
                CHECK.read_line(123, CHECK.time.monotonic() + 1, 4)

    def test_inspect_zero_gate_pre_socket(self):
        """Nonzero or invalid DROP evidence after bridge still forbids the UDP syscall."""
        with patch.object(CHECK, "verify_inner_network"), patch.object(CHECK.nft, "nft_command", return_value=document()), patch.object(CHECK.socket, "socket") as allocate:
            with self.assertRaises(CHECK.baseline.CheckFailure):
                CHECK.inspect_child("net:[200]", "/usr/sbin/nft")
        allocate.assert_not_called()

    def test_udp_requires_eperm_and_positive_exact_counter(self):
        """A send success, another errno, or zero post-counter is never drop evidence."""
        after = zeros()
        after["nftables"][5]["rule"]["expr"][2]["counter"] = {"packets": 1, "bytes": 41}
        for error, evidence, succeeds in ((OSError(CHECK.errno.EPERM, "secret"), after, True),
                                           (OSError(CHECK.errno.EACCES, "secret"), after, False),
                                           (None, after, False),
                                           (OSError(CHECK.errno.EPERM, "secret"), zeros(), False)):
            sender = MagicMock()
            sender.__enter__.return_value = sender
            sender.sendto.side_effect = error
            with patch.object(CHECK, "verify_inner_network"), patch.object(CHECK.nft, "nft_command", side_effect=[zeros(), evidence]), patch.object(CHECK.socket, "socket", return_value=sender):
                if succeeds:
                    self.assertTrue(CHECK.inspect_child("net:[200]", "/usr/sbin/nft")["udp_eperm"])
                else:
                    with self.assertRaises(CHECK.baseline.CheckFailure):
                        CHECK.inspect_child("net:[200]", "/usr/sbin/nft")
            sender.sendto.assert_called_once_with(b"offline-slirp", ("8.8.8.8", 9))

    def test_fixed_network_and_wrong_gateway(self):
        """Exact tap IPv4/default gateway passes, while a wrong gateway blocks inspection."""
        links = [{"ifname": "lo", "flags": []}, {"ifname": "tap0", "flags": ["UP"]}]
        addresses = [{"ifname": "lo", "addr_info": []},
                     {"ifname": "tap0", "addr_info": [{"family": "inet", "local": "10.0.2.100", "prefixlen": 24}]}]
        for gateway in ("10.0.2.2", "10.0.2.3"):
            routes = [{"dst": "default", "dev": "tap0", "gateway": gateway},
                      {"dst": "10.0.2.0/24", "dev": "tap0"}]
            with patch.object(CHECK.baseline, "namespace_guard"), patch.object(CHECK.socket, "if_nameindex", return_value=[(1, "lo"), (2, "tap0")]), patch.object(CHECK.baseline, "verified_ip", return_value="/usr/sbin/ip"), patch.object(CHECK.baseline, "run_ip", side_effect=[links, addresses, routes, []]):
                if gateway == "10.0.2.2":
                    CHECK.verify_inner_network("net:[200]")
                else:
                    with self.assertRaises(CHECK.baseline.CheckFailure):
                        CHECK.verify_inner_network("net:[200]")

    def test_cli_no_arbitrary_commands(self):
        """No executable, address, or command override exists in either mode."""
        with patch("sys.stderr"), patch.object(CHECK.baseline, "initial_guard") as guard:
            with self.assertRaises(SystemExit):
                CHECK.main(["--parent-netns", "net:[100]", "--command", "anything"])
        guard.assert_not_called()


if __name__ == "__main__":
    unittest.main()
