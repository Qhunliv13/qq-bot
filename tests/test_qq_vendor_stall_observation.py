"""Privacy, bounds, identity and fixed-mode regression tests; no QQ execution."""

import io
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from tools import qq_ephemeral_host_entry as host
from tools import qq_vendor_stall_observation as stall


class StallTests(unittest.TestCase):
    """Exercise safe pure projections and the external wait with trusted fakes."""

    def test_fixed_source_and_default_modes(self):
        """The new trusted mode must share exact authored source, not a new JS runner."""
        self.assertEqual(host.fixed_sources("vendor_stall"), host.fixed_sources("vendor_bootstrap"))
        self.assertEqual(host.base_report()["mode"], "ephemeral_wrapper_host_entry")
        self.assertEqual(host.base_report("vendor_bootstrap")["mode"], "ephemeral_vendor_bootstrap")
        self.assertEqual(host.base_report("vendor_stall")["mode"], "ephemeral_vendor_stall")
        with self.assertRaises(host.runtime.ProbeError):
            host.fixed_sources("custom_script")

    def test_proc_stat_projection(self):
        """Discard comm even when it contains spaces or parentheses."""
        tail = ["S"] + ["0"] * 21
        tail[11], tail[12], tail[17], tail[19] = "12", "3", "8", "1234"
        projected = stall.parse_stat("8 (private ) comm) " + " ".join(tail))
        self.assertEqual(projected, {"state": "S", "utime_ticks": 12, "stime_ticks": 3,
                                     "num_threads": 8, "start_ticks": 1234})

    def test_wchan_allowlist(self):
        """Never preserve raw symbols, including unknown or unavailable wchan."""
        for value, expected in (("futex_wait_queue", "futex"), ("do_epoll_wait", "poll"),
                                ("do_wait", "wait"), ("pipe_read", "read"),
                                ("secret_native_symbol", "other"), ("0", "other")):
            self.assertEqual(stall.coarse_wchan(value), expected)

    def test_summary_cautious_classification(self):
        """CPU and wait facts must not be described as proven root cause."""
        rows = [{"identity_verified": True, "elapsed_seconds": second,
                 "main": {"state": "S", "utime_ticks": ticks, "stime_ticks": 0}}
                for second, ticks in ((0, 10), (20, 12))]
        summary = stall.summarize(rows, 100)
        self.assertEqual(summary["classification"], "predominantly_waiting_observed")
        self.assertEqual(summary["cpu_tick_delta"], 2)
        self.assertFalse(summary["root_cause_proven"])
        rows[-1]["main"]["utime_ticks"] = 2010
        self.assertEqual(stall.summarize(rows, 100)["classification"], "busy_cpu_observed")
        self.assertEqual(stall.summarize([], 100)["classification"], "insufficient_observation")

    def test_direct_bounded_categories_and_sizes(self):
        """New stderr-only categories do not change the common classifier."""
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            out.write(b"private stdout")
            err.write(b"Too many open files; File too large; Fontconfig error; sandbox; shared memory")
            report = {}
            stall.capture_categories(out, err, report)
        captured = report["stall_capture"]
        self.assertEqual(captured["stdout_size_bytes"], 14)
        self.assertTrue(captured["stderr_categories"]["too_many_open_files"])
        self.assertTrue(captured["stderr_categories"]["file_size_limit"])
        self.assertFalse(captured["stderr_categories"]["dbus_unavailable"])
        self.assertNotIn("private stdout", str(report))
        self.assertEqual(captured["resource_bounds_unchanged"]["file_size_bytes"], 16384)

    def test_bounded_text(self):
        """Oversized proc-like records fail closed rather than growing capture."""
        fake = mock.MagicMock()
        fake.open.return_value.__enter__.return_value = io.StringIO("abcde")
        with self.assertRaises(ValueError):
            stall.bounded_text(fake, 4)

    def test_wait_bounds_and_restore(self):
        """The external waiter retains <=30 records and restores shared sampler hook."""
        clock = [0.0]
        process = SimpleNamespace(pid=99, args=["/opt/QQ/qq"], returncode=None, poll=lambda: None)
        original = stall.application.sample_identity
        report = {}
        def sleep(seconds):
            """Advance fake time without launching a process or delaying tests."""
            clock[0] += max(seconds, 0.001)
        with mock.patch.object(stall.time, "monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(stall.time, "sleep", side_effect=sleep), \
             mock.patch.object(stall.application, "unprivileged_sample", return_value=None), \
             mock.patch.object(stall.os, "sysconf", return_value=100, create=True):
            with self.assertRaises(subprocess.TimeoutExpired):
                stall.observe_wait(process, {}, report=report)
        self.assertEqual(len(report["stall_observation"]["samples"]), 30)
        self.assertIs(stall.application.sample_identity, original)
        self.assertIsNone(stall.MAIN_PID)
        with self.assertRaises(host.runtime.ProbeError):
            stall.observe_wait(process, {}, timeout=31, report={})

    def test_privacy_source_contract(self):
        """Block forbidden reads/title hooks/actions and verify fixed sampling bounds."""
        source = Path(stall.__file__).read_text(encoding="utf-8")
        for forbidden in ('/mem', '/maps', '/environ', '/cmdline', '/stack', '/session',
                          'XFetchName', 'XGetWMName', '_NET_WM_NAME', 'WM_NAME',
                          'XGetImage', 'XSendEvent', 'XTest', 'ptrace', 'process_vm_readv'):
            self.assertNotIn(forbidden, source)
        self.assertIn('MAX_SAMPLES = 30', source)
        self.assertIn('MAX_THREADS = 64', source)
        self.assertIn('application.unprivileged_sample', source)
        self.assertIn('after != before', source)
        self.assertNotIn('readlink', source)
        self.assertNotIn('XGetWindowProperty', source)

    def test_pid_reuse_refusal(self):
        """A later same-PID identity with a different starttime is rejected early."""
        stall.MAIN_PID, stall.MAIN_START = 42, 101
        identity = {"uid": [65534] * 4, "start_ticks": 102, "cap_eff_zero": True,
                    "no_new_privileges": True, "user_namespace_matches_supervisor": True}
        try:
            with mock.patch.object(stall.application, "process_identity", return_value=identity), \
                 mock.patch.object(stall, "bounded_text") as read:
                self.assertFalse(stall.trusted_sample()["identity_verified"])
                read.assert_not_called()
        finally:
            stall.MAIN_PID, stall.MAIN_START = None, None


if __name__ == "__main__":
    unittest.main()
