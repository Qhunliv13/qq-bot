"""Fake-only connector tests; no socket construction, DNS or external network I/O."""

from dataclasses import replace
import socket
import threading
import unittest
from unittest.mock import Mock, patch

from tools.qq_egress_connector import ConnectorError, connect_pinned
from tools.qq_egress_policy import DestinationPlan, validate_resolved_destination


V4 = "8.8.8.8"
V6 = "2001:4860:4860::8888"


class ConnectorTests(unittest.TestCase):
    """Exercise explicit calls with trusted fakes and forbid resolver entrypoints."""

    def setUp(self):
        """Create a fresh owned fake and prohibit accidental real networking."""
        self.plan = validate_resolved_destination("example.com", 443, [V4, V6], {443})
        self.sock = Mock()
        self.sock.getpeername.return_value = (V4, 443)
        self.factory = Mock(return_value=self.sock)
        for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "create_connection", "socket"):
            guard = patch.object(socket, name, side_effect=AssertionError("network forbidden"))
            guard.start()
            self.addCleanup(guard.stop)

    def call(self, **changes):
        """Keep every test offline through an explicit fake factory and clock."""
        options = dict(timeout_seconds=5, allowed_ports={443}, socket_factory=self.factory,
                       clock=Mock(return_value=10))
        options.update(changes)
        plan = options.pop("plan", self.plan)
        return connect_pinned(plan, **options)

    def failure(self, code, **changes):
        """Assert fixed public errors without leaking network exception strings."""
        with self.assertRaises(ConnectorError) as caught:
            self.call(**changes)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)
        self.assertTrue(caught.exception.__suppress_context__ or caught.exception.__context__ is None)

    def test_v4_success_owner_closes(self):
        """Return exactly the connected socket without closing or sending payloads."""
        result = self.call()
        self.assertIs(result, self.sock)
        self.factory.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        self.sock.connect.assert_called_once_with((V4, 443))
        self.sock.settimeout.assert_called_once_with(5)
        self.sock.close.assert_not_called()
        self.sock.send.assert_not_called()
        result.close()
        self.sock.close.assert_called_once()

    def test_v6_selected_no_fallback(self):
        """Connect only the selected IPv6 literal, accepting canonical peer identity."""
        self.sock.getpeername.return_value = ("2001:4860:4860:0:0:0:0:8888", 443, 0, 0)
        self.call(address_index=1)
        self.factory.assert_called_once_with(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        self.sock.connect.assert_called_once_with((V6, 443))

    def test_all_forged_fields_revalidated(self):
        """A public frozen dataclass constructor must not bypass any policy field."""
        invalid = [object(), replace(self.plan, host="bad@host"),
                   replace(self.plan, host="EXAMPLE.COM"), replace(self.plan, port=True),
                   replace(self.plan, port=0), replace(self.plan, addresses=(V4, "127.0.0.1")),
                   replace(self.plan, addresses=[V4]), replace(self.plan, addresses=()),
                   replace(self.plan, addresses=(V4,) * 17),
                   replace(self.plan, addresses=(V4, V4)),
                   replace(self.plan, addresses=("2001:4860:4860:0:0:0:0:8888",)),
                   DestinationPlan("1.1.1.1", 443, (V4,))]
        for plan in invalid:
            with self.subTest(plan=plan):
                self.failure("invalid_plan", plan=plan)
        self.factory.assert_not_called()

    def test_allowed_ports_explicit_and_revalidated(self):
        """The caller's port bound is required again, including explicit deny-all."""
        for allowed in ({80}, set(), {True}, [443]):
            self.failure("invalid_plan", allowed_ports=allowed)
        self.factory.assert_not_called()
        self.call(allowed_ports=None)
        with self.assertRaises(TypeError):
            connect_pinned(self.plan, timeout_seconds=5, socket_factory=self.factory)

    def test_invalid_timeout_and_index(self):
        """Reject booleans, nonfinite values, coercions and out-of-range indices."""
        for value in (True, None, "5", 0, 0.9, 31, float("nan"), float("inf"), 10**1000):
            self.failure("invalid_timeout", timeout_seconds=value)
        for value in (True, -1, 2, "0", None):
            self.failure("invalid_address_index", address_index=value)
        self.factory.assert_not_called()

    def test_factory_and_connect_errors(self):
        """Sanitize construction failure and close a failed single connection."""
        self.factory.side_effect = OSError("secret network detail")
        self.failure("factory_failed")
        self.sock.close.assert_not_called()
        self.factory.side_effect = None
        self.sock.connect.side_effect = OSError("secret destination")
        self.failure("connect_failed")
        self.sock.connect.assert_called_once()
        self.sock.close.assert_called_once()

    def test_timeout_and_settimeout_failure(self):
        """Timeout and preconnect configuration failures both release ownership."""
        self.sock.settimeout.side_effect = OSError("private detail")
        self.failure("connect_failed")
        self.sock.connect.assert_not_called()
        self.sock.close.assert_called_once()
        self.sock.reset_mock()
        self.sock.settimeout.side_effect = None
        self.sock.connect.side_effect = TimeoutError("private detail")
        self.failure("deadline_exceeded")
        self.sock.close.assert_called_once()

    def test_peer_identity_and_shape(self):
        """Reject wrong family/IP/port, booleans, scopes, zones and malformed tuples."""
        for index, peer in ((0, ("1.1.1.1", 443)), (0, (V4, 80)), (0, (V4, True)),
                            (0, [V4, 443]), (0, (V4, 443, 0, 0)), (0, (b"8.8.8.8", 443)),
                            (0, ("::ffff:8.8.8.8", 443)), (1, (V6, 443)),
                            (1, (V6, 443, 0, 1)), (1, (V6, 443, 0, False)),
                            (1, (V6 + "%0", 443, 0, 0)), (1, (V6, 443, 1, 0))):
            with self.subTest(peer=peer):
                self.sock.reset_mock()
                self.sock.getpeername.return_value = peer
                self.failure("peer_mismatch", address_index=index)
                self.sock.close.assert_called_once()

    def test_peer_failure_closes(self):
        """A getpeername error cannot hand an unverified socket to the caller."""
        self.sock.getpeername.side_effect = OSError("secret peer")
        self.failure("peer_failed")
        self.sock.close.assert_called_once()

    def test_deadline_pre_post_and_remaining_budget(self):
        """Account for elapsed time and reject success arriving after the deadline."""
        self.failure("deadline_exceeded", clock=Mock(side_effect=[10, 15]))
        self.factory.assert_not_called()
        self.failure("deadline_exceeded", clock=Mock(side_effect=[10, 11, 12, 15]))
        self.sock.settimeout.assert_called_once_with(3)
        self.sock.close.assert_called_once()
        self.sock.reset_mock()
        self.failure("deadline_exceeded", clock=Mock(side_effect=[10, 11, 12, 13, 15]))
        self.sock.close.assert_called_once()

    def test_stop_before_and_after_connect(self):
        """Cooperative cancellation closes late connections but opens none if pre-set."""
        event = threading.Event()
        event.set()
        self.failure("cancelled", stop_event=event)
        self.factory.assert_not_called()
        event.clear()
        self.sock.connect.side_effect = lambda target: event.set()
        self.failure("cancelled", stop_event=event)
        self.sock.close.assert_called_once()

    def test_stop_after_peer_and_clock_error_cleanup(self):
        """The final boundary still rejects cancellation or a failing injected clock."""
        event = threading.Event()

        def peer_and_cancel():
            """Simulate cancellation during the otherwise nonblocking peer lookup."""
            event.set()
            return (V4, 443)

        self.sock.getpeername.side_effect = peer_and_cancel
        self.failure("cancelled", stop_event=event)
        self.sock.close.assert_called_once()
        self.sock.reset_mock()
        self.sock.getpeername.side_effect = None
        self.failure("clock_failed", clock=Mock(side_effect=[10, 10, 10, OSError("private")]))
        self.sock.close.assert_called_once()

    def test_invalid_dependencies_and_clock(self):
        """Fail closed on malformed trusted API inputs and non-monotonic clocks."""
        self.failure("invalid_dependency", socket_factory=None)
        self.failure("invalid_dependency", clock=None)
        self.failure("invalid_stop_event", stop_event=object())
        for value in (float("nan"), float("inf"), True, "10"):
            self.failure("clock_failed", clock=Mock(return_value=value))
        self.failure("clock_failed", clock=Mock(side_effect=[10, 9]))
        self.factory.assert_not_called()

    def test_interrupt_cleanup_and_close_failure(self):
        """Interrupts propagate after cleanup; close failure is explicit, not hidden."""
        self.sock.connect.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.call()
        self.sock.close.assert_called_once()
        self.sock.connect.side_effect = OSError("secret")
        self.sock.close.side_effect = OSError("secret close")
        self.failure("close_failed")


if __name__ == "__main__":
    unittest.main()
