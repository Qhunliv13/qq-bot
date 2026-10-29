"""Fake-only logging contrast contracts; never execute native QQ from tests."""
import hashlib
import io
import json
import sys
from unittest import mock

import pytest
import test_qq_host_fatal_control as fatal_tests
import test_qq_host_resources_control as resources
from tools import qq_ephemeral_host_entry as shared
from tools import qq_host_logging_control as logging
from tools import qq_vendor_stall_observation as stall

observer = resources.observer
imports = resources.imports
MODE = "host_dispatch_logging_offline"
BASELINE = "host_dispatch_fatal_offline"


def test_source_and_bounds():
    """Pin identical authored bytes and unchanged limits rather than claiming equal timing."""
    assert shared.fixed_sources(MODE) == shared.fixed_sources(BASELINE)
    assert hashlib.sha256(shared.fixed_sources(MODE)[shared.MAIN].read_bytes()).hexdigest() == resources.identity_tests.SOURCE_HASH
    assert (shared.LIMIT, shared.runtime.HELPER_OUTPUT_LIMIT, shared.QQ_TIMEOUT, shared.OUTER_TIMEOUT) == (16384, 131072, 30, 45)
    fatal_tests.test_full_envelope_bound()


@pytest.mark.parametrize("mode", [BASELINE, MODE, "host_dispatch_exit_offline", "host_dispatch_waits_offline",
    "host_dispatch_resources_offline", "host_dispatch_identity_offline", "host_dispatch_capture_offline",
    "host_dispatch_roles_offline", "host_dispatch_offline", *resources.integration.MODES])
def test_only_fixed_logging_environment(monkeypatch, observer, mode):
    """Fresh runtime env excludes ambient logging; legacy invocation kwargs remain exact."""
    monkeypatch.setenv("ELECTRON_ENABLE_LOGGING", "ambient-secret")
    assert "ELECTRON_ENABLE_LOGGING" not in shared.runtime.minimal_environment()
    monkeypatch.setitem(sys.modules, "qq_host_dispatch_control", resources.dispatch)
    waiter, capture = mock.Mock(), mock.Mock()
    monkeypatch.setattr(stall, "observe_wait", waiter)
    monkeypatch.setattr(stall, "capture_categories", capture)
    out, err = io.BytesIO(b"private-out"), io.BytesIO(b"private-err")
    report = resources.integration.invoke(mode, out, err)
    env = {"QQ_WRAPPER_HOST_NONCE": "a" * 32}
    if mode == MODE:
        env["ELECTRON_ENABLE_LOGGING"] = "1"
    assert shared.subprocess.Popen.call_args == mock.call(
        ["/opt/QQ/qq"], env=env, stdin=shared.subprocess.DEVNULL,
        stdout=out, stderr=err, close_fds=True, preexec_fn=shared.child_limits)
    if mode in (BASELINE, MODE, "host_dispatch_exit_offline"):
        waiter.assert_called_once_with(observer, env, timeout=30, report=report,
                                       detailed_resources=True, wait_symbols=True, exit_status=True)
        capture.assert_called_once_with(out, err, report, detailed=True)
    if mode in (BASELINE, MODE):
        assert report["_private_fatal_capture"] == shared.private_fatal_envelope(out, err)
    else:
        assert "_private_fatal_capture" not in report


def test_identity_and_preimports(monkeypatch, tmp_path, imports):
    """New mode alone joins existing strict synthetic gate and identical helper preimports."""
    monkeypatch.setattr(fatal_tests, "MODE", MODE)
    fatal_tests.test_identity(monkeypatch, tmp_path, imports)


def test_copied_helpers(monkeypatch, tmp_path):
    """Exercise actual outer staging with fake launch and pinned private reservation only."""
    monkeypatch.setitem(resources.integration.HELPERS, MODE, resources.identity_tests.HELPERS)
    original = shared.observe
    def observe(*args, **kwargs):
        """Supply required consent while reusing the existing staged-copy assertion."""
        return original(*args, **kwargs, private_capture_path=str(tmp_path / "private.json"))
    monkeypatch.setattr(shared, "observe", observe)
    monkeypatch.setattr(shared.runtime, "open_private_diagnostics", lambda _path: None)
    persist = mock.Mock()
    monkeypatch.setattr(shared, "persist_private_fatal", persist)
    resources.integration.test_outer_copy_set_matches_preimports(monkeypatch, tmp_path, MODE)
    persist.assert_called_once()


def test_private_gate_and_exclusive(monkeypatch, tmp_path):
    """Fixed CLI rejects missing consent/overwrites before execution and selects only this mode."""
    monkeypatch.setattr(fatal_tests, "fatal", logging)
    fatal_tests.test_exclusive_and_missing_path(tmp_path, monkeypatch)
    monkeypatch.undo()
    monkeypatch.setattr(fatal_tests, "MODE", MODE)
    fatal_tests.test_mode_gate(monkeypatch)
    invoke = mock.Mock(return_value={"status": "not_executed"})
    monkeypatch.setattr(shared, "observe", invoke)
    logging.observe("unused", private_capture_path="/tmp/private")
    invoke.assert_called_once_with("unused", execute=False, control_mode=MODE, private_capture_path="/tmp/private")


@pytest.mark.parametrize("size", [0, 156, 16384, 16385, 32768])
def test_private_roundtrip_no_leak(tmp_path, size, monkeypatch):
    """The same shared private pop/validation path remains bounded for logging output."""
    fatal_tests.test_bounded_roundtrip(tmp_path, size, monkeypatch)


def test_frozen_actual_logging_capture():
    """Assert public facts from one closed run without opening private text or rerunning QQ."""
    if not logging.ARTIFACT.exists():
        pytest.skip("actual capture requires explicit parent GO")
    current = json.loads(logging.ARTIFACT.read_text(encoding="utf-8"))
    baseline = json.loads(logging.ARTIFACT.with_name("qq-host-fatal-linux-evidence.json").read_text(encoding="utf-8"))
    assert current["mode"] == "ephemeral_" + MODE
    for key in ("authored_source_sha256", "synthetic_identity", "invocation", "bounds_seconds", "isolation"):
        assert current[key] == baseline[key]
    assert current["host_software_sha256_before"] == current["host_software_sha256_after"] == baseline["host_software_sha256_before"]
    assert current["stall_capture"]["resource_bounds_unchanged"] == baseline["stall_capture"]["resource_bounds_unchanged"]
    samples = current["stall_observation"]["samples"]
    assert len(samples) == 30 and all(item["identity_verified"] for item in samples)
    assert [item["main"]["core_dumping"] for item in samples] == [0] + [1] * 29
    assert [item["main"]["exit_code"] for item in samples] == [0] + [5] * 29
    assert current["private_fatal_capture"]["stdout"]["captured_length"] == 156
    assert current["private_fatal_capture"]["stderr"]["captured_length"] == 746
    assert not current["stall_capture"]["stdout_cap_reached"]
    assert not current["stall_capture"]["stderr_cap_reached"]
    assert not current["stall_capture"]["stderr_prefix_truncated"]
    assert not current["stall_capture"]["stderr_categories"]["file_size_limit"]
    assert current["private_export"]["completed"] and current["private_export"]["linux_owner_mode_verified"]
    assert current["completion"] == "watchdog_30s" and current["exit_code"] == -9
    assert current["errors"] == ["host_dispatch_incomplete"]
    assert "_private_fatal_capture" not in current and "base64" not in json.dumps(current)
    assert not current["safe_to_login"] and not current["native_binding_verified"]
