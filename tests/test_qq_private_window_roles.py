"""Mocked bounded Xlib metadata tests; no display, process launch or QQ execution."""

import ctypes
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from tools import qq_private_window_roles as roles
from tools import qq_ephemeral_host_entry as host


def identity():
    """Return the exact safe main identity expected by the read-only observer."""
    return {"pid": 42, "exe": "/opt/QQ/qq", "start_ticks": 123, "uid": [65534] * 4,
            "cap_eff_zero": True, "no_new_privileges": True,
            "user_namespace_matches_supervisor": True}


class RoleTests(unittest.TestCase):
    """Exercise bounded formats, strict attribution and privacy-only projections."""

    def test_classes_only_boolean_allowlist(self):
        """Unknown class bytes must never reach reports as raw strings."""
        result = roles.class_flags(b"SecretQQ\0error-dialog-private")
        self.assertEqual(result, {"class_contains_error": True, "class_contains_dialog": True,
                                  "class_contains_qq": True})
        self.assertNotIn("private", str(result))
        with self.assertRaises(ValueError):
            roles.class_flags(b"x" * 129)

    def test_property_bounded_format_and_free(self):
        """Reject wrong formats/counts/errors and release buffers on every path."""
        reader = object.__new__(roles.PrivateWindowRoles)
        reader.display = 1
        storage = (ctypes.c_ulong * 2)(99, 100)
        state = {"format": 32, "count": 2, "remaining": 8, "status": 0}
        calls = []
        def get_property(display, window, atom, offset, length, delete, kind,
                         actual, fmt, count, remaining, data):
            """Populate fake Xlib outputs without a real X server."""
            calls.append((length, delete, kind))
            actual._obj.value, fmt._obj.value = roles.XA_ATOM, state["format"]
            count._obj.value, remaining._obj.value = state["count"], state["remaining"]
            data._obj.value = ctypes.addressof(storage)
            return state["status"]
        reader.lib = SimpleNamespace(XGetWindowProperty=get_property, XFree=mock.Mock())
        self.assertEqual(reader.property_value(5, 8, roles.XA_ATOM, 32, 128), ([99, 100], True))
        self.assertEqual(calls[0], (128, 0, roles.XA_ATOM))
        state["format"] = 8
        self.assertEqual(reader.property_value(5, 8, roles.XA_ATOM, 32, 128), (None, True))
        state.update(format=32, count=129)
        self.assertEqual(reader.property_value(5, 8, roles.XA_ATOM, 32, 128), (None, True))
        state.update(count=2, status=1)
        self.assertEqual(reader.property_value(5, 8, roles.XA_ATOM, 32, 128), (None, True))
        self.assertEqual(reader.lib.XFree.call_count, 4)

    def test_class_property_requests_only_128_bytes(self):
        """Format-8 Xlib requests use 32 four-byte units, never 128 units."""
        reader = object.__new__(roles.PrivateWindowRoles)
        reader.display = 1
        raw = ctypes.create_string_buffer(b"QQ\0secret")
        def get_property(*args):
            """Return one bounded class buffer with an explicit truncated suffix."""
            self.assertEqual(args[4], 32)
            args[7]._obj.value, args[8]._obj.value = roles.XA_STRING, 8
            args[9]._obj.value, args[10]._obj.value = 9, 20
            args[11]._obj.value = ctypes.addressof(raw)
            return 0
        reader.lib = SimpleNamespace(XGetWindowProperty=get_property, XFree=mock.Mock())
        value, truncated = reader.property_value(1, 2, roles.XA_STRING, 8, 128)
        self.assertEqual(value, b"QQ\0secret")
        self.assertTrue(truncated)

    def test_identity_mismatch_refuses_before_property_reads(self):
        """PID reuse and nonmatching actual-main identities fail before traversing X."""
        reader = object.__new__(roles.PrivateWindowRoles)
        reader.snapshot = mock.Mock()
        with mock.patch.object(roles.application, "process_identity", return_value=dict(identity(), start_ticks=124)):
            with self.assertRaises(ValueError):
                reader.role_snapshot(identity())
        reader.snapshot.assert_not_called()
        with self.assertRaises(ValueError):
            reader.role_snapshot(dict(identity(), uid=[0] * 4))

    def test_attribution_uses_actual_main_only_and_no_ids_escape(self):
        """Other QQ processes cannot create a main-owned-dialog observation."""
        reader = object.__new__(roles.PrivateWindowRoles)
        reader.snapshot = mock.Mock(return_value={"root_children": 2, "visited_windows": 2,
            "qq_pid_properties": [{"window_id": 100, "identity": identity()},
                                   {"window_id": 200, "identity": dict(identity(), pid=43)}]})
        reader.window_roles = mock.Mock(return_value={"type_dialog": True,
                                  "transient_for_main_correlated_window": False})
        with mock.patch.object(roles.application, "process_identity", return_value=identity()):
            result = reader.role_snapshot(identity())
        reader.window_roles.assert_called_once_with(100, {100})
        self.assertEqual(result["main_pid_correlated_windows"], 1)
        self.assertTrue(result["main_owned_dialog_observed"])
        self.assertNotIn("window_id", str(result))
        self.assertFalse(result["modal_state_proven"])

    def test_source_privacy_contract_and_fixed_mode(self):
        """Prohibit text/image/input/memory APIs and arbitrary PID/display arguments."""
        source = Path(roles.__file__).read_text(encoding="utf-8")
        for forbidden in ("WM_NAME", "_NET_WM_NAME", "XFetchName", "XGetImage", "XSendEvent",
                          "XTest", "/mem", "/maps", "/environ", "/cmdline", "ptrace",
                          'add_argument("--display', 'add_argument("--pid'):
            self.assertNotIn(forbidden, source)
        self.assertEqual(host.fixed_sources("vendor_window_roles"), host.fixed_sources("vendor_stall"))
        self.assertEqual(host.base_report("vendor_window_roles")["mode"], "ephemeral_vendor_window_roles")


if __name__ == "__main__":
    unittest.main()
