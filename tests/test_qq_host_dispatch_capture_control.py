"""Fake-only bounded capture/privacy and exact dispatch routing regression tests."""
import hashlib
import io
import json
import sys
import tempfile
from unittest import mock

import pytest
import test_qq_prepared_stall_integration as integration
from tools import qq_ephemeral_host_entry as shared
from tools import qq_host_dispatch_control as dispatch
from tools import qq_host_dispatch_capture_control as capture_control
from tools import qq_vendor_stall_observation as stall

imports = integration.imports
observer = integration.observer
MODE = "host_dispatch_capture_offline"
HELPERS = ("qq_host_dispatch_control", "qq_native_connect_control",
           "qq_application_observation", "qq_vendor_stall_observation")


def test_fixed_control_and_exclusive_output(tmp_path, monkeypatch):
    """No mode/source/output injection or overwrite may invoke the native runner."""
    assert shared.fixed_sources(MODE) == shared.fixed_sources("host_dispatch_offline")
    assert hashlib.sha256(shared.fixed_sources(MODE)[shared.MAIN].read_bytes()).hexdigest() == "4ab8582d780a2cbf4a1fdddc90990dc0c74d594edc8f177dfe219b2b61993a94"
    assert capture_control.ARTIFACT.name == "qq-host-dispatch-capture-evidence.json"
    invoke = mock.Mock(return_value={"status": "not_executed"})
    monkeypatch.setattr(shared, "observe", invoke)
    capture_control.observe("unused")
    invoke.assert_called_once_with("unused", execute=False, control_mode=MODE)
    artifact = tmp_path / "existing.json"
    artifact.write_text("preserved", encoding="utf-8")
    monkeypatch.setattr(capture_control, "ARTIFACT", artifact)
    invoke.reset_mock()
    with pytest.raises(FileExistsError):
        capture_control.main(["--qq-root", "unused", "--execute"])
    invoke.assert_not_called()
    assert artifact.read_text(encoding="utf-8") == "preserved"
    for option in ("--control-mode", "--source", "--output", "--no-sandbox"):
        with pytest.raises(SystemExit):
            capture_control.main(["--qq-root", "unused", option, "secret"])
    invoke.assert_not_called()


@pytest.mark.parametrize("mode", ["host_dispatch_offline", "host_dispatch_roles_offline", MODE])
def test_exact_routing(monkeypatch, observer, mode):
    """Only capture mode adds detailed capture, while preserving parser and waiter."""
    monkeypatch.setitem(sys.modules, "qq_host_dispatch_control", dispatch)
    waiter, capture = mock.Mock(), mock.Mock()
    monkeypatch.setattr(stall, "observe_wait", waiter)
    monkeypatch.setattr(stall, "capture_categories", capture)
    project = mock.Mock(wraps=dispatch.project_report)
    monkeypatch.setattr(dispatch, "project_report", project)
    out, err = io.BytesIO(), io.BytesIO()
    report = integration.invoke(mode, out, err)
    assert report["mode"] == "ephemeral_" + mode
    assert report["private_output_categories"] == {}
    project.assert_called_once_with(report, None, -9)
    if mode == MODE:
        capture.assert_called_once_with(out, err, report, detailed=True)
    else:
        capture.assert_not_called()
    if mode != "host_dispatch_roles_offline":
        assert "window_sampler" not in waiter.call_args.kwargs


def test_preimports_and_copydeps(monkeypatch, tmp_path, imports):
    """The new mode copies exactly the old helpers with no role dependency."""
    monkeypatch.setitem(sys.modules, "qq_host_dispatch_control", dispatch)
    monkeypatch.setitem(integration.HELPERS, MODE, HELPERS)
    integration.test_helpers_preimported_before_pivot(monkeypatch, tmp_path, imports, MODE)
    integration.test_outer_copy_set_matches_preimports(monkeypatch, tmp_path / "outer", MODE)


@pytest.mark.parametrize("raw,key", [
    (b"Failed to connect /run/dbus/system_bus_socket: No such file or directory", "system_bus_socket_missing"),
    (b"/run/dbus/system_bus_socket ENOENT", "system_bus_socket_missing"),
    (b"Failed to connect to /tmp/runtime/bus", "private_session_bus_connect_failure"),
    (b"D-Bus connection failed", "dbus_scope_unknown"),
    (b"EMFILE", "resource_emfile"), (b"EFBIG", "resource_efbig"),
    (b"ENOSPC", "resource_enospc"),
    (b"failed to create profile /secret/account", "profile_create_or_open_failed"),
    (b"FATAL: sandbox initialization failed", "sandbox_fatal"),
])
def test_positive_tokens(raw, key):
    """Explicit bounded tokens distinguish each fixed observation enum."""
    assert stall.detailed_categories(raw)[key]


@pytest.mark.parametrize("raw", [
    b"/run/dbus/system_bus_socket\nENOENT",
    b"/run/dbus/system_bus_socket.extra ENOENT",
    b"prefix/run/dbus/system_bus_socket ENOENT",
    b"/tmp/runtime/bus\nfailed to connect",
    b"failed to connect /tmp/runtime/bus-extra",
    b"xEMFILE EFBIGsuffix xENOSPC nonfatal sandbox enabled",
    b"failed to create\nprofile; FATAL\nsandbox",
    b"sandbox enabled; profile opened successfully",
])
def test_no_substring_or_crossline_false_positives(raw):
    """Scoped failures require exact paths/tokens on the same bounded line."""
    result = stall.detailed_categories(raw)
    assert not any(value for key, value in result.items() if key != "dbus_scope_unknown")


def test_scoped_and_unknown_lines():
    """A scoped D-Bus error is not unknown; a separate generic D-Bus line is."""
    raw = b"dbus /run/dbus/system_bus_socket ENOENT"
    assert not stall.detailed_categories(raw)["dbus_scope_unknown"]
    assert stall.detailed_categories(raw + b"\ndbus failure unknown")["dbus_scope_unknown"]
    assert not stall.detailed_categories(b"DBus connected successfully")["dbus_scope_unknown"]


@pytest.mark.parametrize("size", [0, 16383, 16384, 16385])
def test_sizes_cap_truncation_default_compatibility(size):
    """Equality means cap reached but not truncated; stdout is never read."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        out.write(b"s" * size)
        err.write(b"x" * size)
        report, old, explicit_default = {}, {}, {}
        stall.capture_categories(out, err, old)
        stall.capture_categories(out, err, explicit_default, detailed=False)
        assert old == explicit_default
        stall.capture_categories(out, err, report, detailed=True)
    captured = report["stall_capture"]
    assert captured["stdout_size_bytes"] == captured["stderr_size_bytes"] == size
    assert captured["stdout_cap_reached"] == captured["stderr_cap_reached"] == (size >= 16384)
    assert captured["stderr_prefix_truncated"] == (size > 16384)
    assert "stdout_prefix_truncated" not in captured
    assert not captured["category_false_means_no_error"] and not captured["root_cause_proven"]
    assert set(old["stall_capture"]) == {"stderr_categories", "stderr_prefix_truncated", "stdout_size_bytes", "stderr_size_bytes", "resource_bounds_unchanged"}


def test_secret_paths_args_stdout_and_outside_prefix_not_returned():
    """Read only bounded stderr, projecting no raw paths, arguments or output text."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        out.write(b"EMFILE --secret=private-argument /secret-account")
        err.write(b"/secret-account --secret=private-argument\n".ljust(shared.LIMIT, b"x") + b"\nENOSPC")
        report = {}
        with mock.patch.object(out, "read", side_effect=AssertionError("stdout read forbidden")):
            stall.capture_categories(out, err, report, detailed=True)
    serialized = json.dumps(report)
    assert "secret-account" not in serialized and "private-argument" not in serialized
    assert not report["stall_capture"]["stderr_categories"]["resource_emfile"]
    assert not report["stall_capture"]["stderr_categories"]["resource_enospc"]
