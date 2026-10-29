"""Fake-only guard tests: no namespace changes, commands or real sockets."""

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

# Import the explicit fixture by file without invoking its __main__ entrypoint.
SPEC = importlib.util.spec_from_file_location(
    "egress_connector_posix_check", Path(__file__).with_name("egress_connector_posix_check.py"))
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)
PARENT = "net:[100]"
V4_HEADER = "Iface\tDestination\tGateway\tFlags\n"
REJECT = " ".join(["0" * 32, "00", "0" * 32, "00", "0" * 32,
                   "ffffffff", "00000000", "00000000", "00200200", "lo"])


class GuardTests(unittest.TestCase):
    """All negative guards must reject before any externally supplied runner call."""

    def setUp(self):
        """Replace every host-sensitive observation and forbid accidental sockets."""
        self.runner = Mock()
        self.patches = [
            patch.object(CHECK.os, "readlink", side_effect=["net:[200]", "net:[1]"]),
            patch.object(CHECK.socket, "if_nameindex", return_value=[(1, "lo")]),
            patch.object(CHECK, "loopback_flags", return_value=8),
            patch.object(CHECK.Path, "read_text", side_effect=[V4_HEADER, REJECT]),
            patch.object(CHECK.socket, "socket", side_effect=AssertionError("socket forbidden")),
            patch.object(CHECK.subprocess, "run", side_effect=AssertionError("command forbidden")),
        ]
        self.mocks = [item.start() for item in self.patches]
        for item in self.patches:
            self.addCleanup(item.stop)

    def denied(self, stage):
        """Assert a static failure and zero runner calls for initial unsafe states."""
        with self.assertRaises(CHECK.CheckFailure) as caught:
            CHECK.configure(PARENT, runner=self.runner)
        self.assertEqual(str(caught.exception), stage)
        self.runner.assert_not_called()

    def test_parent_match(self):
        """A caller still in its launcher's namespace must not issue commands."""
        self.mocks[0].side_effect = [PARENT]
        self.denied("parent_namespace")

    def test_init_match(self):
        """Visible init namespace equality must fail independently of parent."""
        self.mocks[0].side_effect = ["net:[200]", "net:[200]"]
        self.denied("init_namespace")

    def test_missing_interfaces(self):
        """An empty interface enumeration cannot establish an isolated loopback."""
        self.mocks[1].return_value = []
        self.denied("initial_links")

    def test_external_interface(self):
        """An additional interface fails even when loopback is present."""
        self.mocks[1].return_value = [(1, "lo"), (2, "eth0")]
        self.denied("initial_links")

    def test_loopback_up(self):
        """An already-UP loopback is not the required fresh state."""
        self.mocks[2].return_value = 9
        self.denied("initial_flags")

    def test_unsafe_ipv4_route(self):
        """Any initial IPv4 route rejects before setup commands."""
        self.mocks[3].side_effect = [V4_HEADER + "lo 00000000 00000000 0001\n", REJECT]
        self.denied("initial_routes")

    def test_unsafe_ipv6_route(self):
        """A usable IPv6 default is not a harmless kernel reject placeholder."""
        self.mocks[3].side_effect = [V4_HEADER, REJECT.replace("00200200", "00000001")]
        self.denied("initial_routes")

    def test_empty_ipv4_table(self):
        """Accept the observed zero-byte empty table without running any command."""
        self.mocks[3].side_effect = ["", REJECT]
        CHECK.initial_guard(PARENT)
        self.runner.assert_not_called()

    def test_malformed_nonempty_ipv4_table(self):
        """Empty-table compatibility must not accept whitespace or malformed data."""
        for text in (" ", "\n", "\t\n", "broken header\n", "lo 00000000 00000000 0001\n"):
            with self.subTest(text=text):
                self.mocks[0].side_effect = ["net:[200]", "net:[1]"]
                self.mocks[3].side_effect = [text, REJECT]
                self.denied("initial_routes")

    def test_safe_initial_state(self):
        """Fresh loopback with only IPv6 reject placeholders passes read-only checks."""
        CHECK.initial_guard(PARENT)
        self.runner.assert_not_called()

    def test_policy_rejections_no_allocation(self):
        """The genuine policy rejects both cases without allocating any TCP socket."""
        self.assertEqual(CHECK.policy_rejections(), 2)
        self.mocks[4].assert_not_called()

    def test_preferred_ip_precedes_which(self):
        """An existing absolute system ip path must not consult PATH discovery."""
        with patch.object(CHECK.Path, "exists", return_value=True), patch.object(CHECK.Path, "resolve", return_value=Path("/usr/sbin/ip")), patch.object(CHECK.Path, "stat", return_value=Mock(st_mode=0o100755)), patch.object(CHECK.os.path, "isabs", return_value=True), patch.object(CHECK.os, "access", return_value=True), patch.object(CHECK.shutil, "which") as which:
            self.assertEqual(CHECK.verified_ip(), str(Path("/usr/sbin/ip")))
        which.assert_not_called()

    def test_commands_exactly_bounded(self):
        """The sole mutation surface is three fixed loopback commands."""
        self.assertEqual(CHECK.WRITE_COMMANDS, (
            ("address", "add", "8.8.8.8/32", "dev", "lo"),
            ("-6", "address", "add", "2001:4860:4860::8888/128", "nodad", "dev", "lo"),
            ("link", "set", "dev", "lo", "up"),
        ))
        for args in (("route", "add", "default"), ("link", "set", "eth0", "up"), ("-batch", "anything")):
            with self.assertRaises(CHECK.CheckFailure):
                CHECK.run_ip("/usr/sbin/ip", args)
        self.mocks[5].assert_not_called()

    def test_configuration_order(self):
        """All setup writes are fixed and postverification is mandatory afterward."""
        with patch.object(CHECK, "verified_ip", return_value="/usr/sbin/ip"), patch.object(CHECK, "verify_configured") as verify:
            CHECK.configure(PARENT, runner=self.runner)
        self.assertEqual([call.args for call in self.runner.call_args_list],
                         [("/usr/sbin/ip", args) for args in CHECK.WRITE_COMMANDS])
        verify.assert_called_once_with(PARENT, "/usr/sbin/ip", self.runner)

    def test_unsafe_postroute_stops_kernel(self):
        """A postconfiguration rejection must never reach the socket test stage."""
        with patch.object(CHECK, "configure", side_effect=CHECK.CheckFailure("verified_routes")), patch.object(CHECK, "kernel_checks") as kernel, patch("builtins.print"):
            self.assertEqual(CHECK.main(["--parent-netns", PARENT]), 1)
        kernel.assert_not_called()


class RouteTests(unittest.TestCase):
    """Validate exact local/connected allowlists without invoking any runner."""

    def route(self, destination, kind="local", table="local", **extra):
        """Build a minimal iproute2 record with an explicit kernel origin."""
        return dict(dst=destination, type=kind, table=table, dev="lo", protocol="kernel", **extra)

    def test_exact_v4(self):
        """An exact host-scoped IPv4 test literal is accepted."""
        CHECK.check_routes([self.route(CHECK.V4, scope="host")], 4)

    def test_exact_v6_connected(self):
        """IPv6 /128 connected and local routes can coexist on loopback only."""
        CHECK.check_routes([self.route(CHECK.V6), self.route(CHECK.V6 + "/128", "unicast", "main"),
                            self.route("::1/128", "unicast", "main"), self.route("::1")], 6)

    def test_unsafe_routes(self):
        """Defaults, gateways, external links and wrong scopes all fail closed."""
        base = self.route(CHECK.V4, scope="host")
        unsafe = [self.route("default", "unicast", "main"),
                  dict(base, gateway="1.1.1.1"), dict(base, dev="eth0"),
                  dict(base, scope="link"), dict(base, multipath=[]),
                  self.route("8.8.4.4", scope="host")]
        for route in unsafe:
            with self.subTest(route=route), self.assertRaises(CHECK.CheckFailure):
                CHECK.check_routes([base, route], 4)


if __name__ == "__main__":
    unittest.main()
