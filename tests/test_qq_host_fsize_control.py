"""Fake-only fixed FSIZE contrast contracts; no native launch from tests."""
import functools
import hashlib
import io
import json
import sys
import tempfile
from unittest import mock

import pytest
import test_qq_host_fatal_control as fatal_tests
import test_qq_host_logging_control as logging_tests
import test_qq_host_resources_control as resources
from tools import qq_ephemeral_host_entry as shared
from tools import qq_host_fsize_control as fsize
from tools import qq_vendor_stall_observation as stall

observer = resources.observer
imports = resources.imports
MODE = "host_dispatch_fsize_offline"


def test_source_bounds():
    """The fixed source and all capture/watchdog limits remain identical."""
    assert shared.fixed_sources(MODE) == shared.fixed_sources(logging_tests.MODE)
    assert hashlib.sha256(shared.fixed_sources(MODE)[shared.MAIN].read_bytes()).hexdigest() == resources.identity_tests.SOURCE_HASH
    assert (shared.LIMIT, shared.runtime.OUTPUT_LIMIT, shared.runtime.HELPER_OUTPUT_LIMIT,
            shared.QQ_TIMEOUT, shared.OUTER_TIMEOUT) == (16384, 1048576, 131072, 30, 45)


@pytest.mark.parametrize("value", [None, True, 0, 65536, 1048577, "1048576", 1048576.0])
def test_invalid_limits(monkeypatch, value):
    """No general relaxation or bool/float aliases may reach setrlimit."""
    resource = mock.Mock()
    monkeypatch.setitem(sys.modules, "resource", resource)
    with pytest.raises(shared.runtime.ProbeError, match="child_file_size_profile_invalid"):
        shared.child_limits(file_size_bytes=value)
    resource.setrlimit.assert_not_called()


@pytest.mark.parametrize("value", [16384, 1048576])
def test_fixed_limits(monkeypatch, value):
    """Both profiles share the exact UID/capability/NNP implementation."""
    resource, drop = mock.Mock(), mock.Mock()
    monkeypatch.setitem(sys.modules, "resource", resource)
    monkeypatch.setattr(shared.runtime, "drop_runtime_privileges", drop)
    shared.child_limits(file_size_bytes=value)
    resource.setrlimit.assert_called_once_with(resource.RLIMIT_FSIZE, (value, value))
    drop.assert_called_once_with()


def test_invocation(monkeypatch, observer):
    """Only new mode gets partial; actual supervisor limits and same logger/waiter are used."""
    resource = mock.Mock()
    resource.getrlimit.return_value = (1048576, 1048576)
    monkeypatch.setitem(sys.modules, "resource", resource)
    monkeypatch.setitem(sys.modules, "qq_host_dispatch_control", resources.dispatch)
    waiter = mock.Mock()
    monkeypatch.setattr(stall, "observe_wait", waiter)
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        report = resources.integration.invoke(MODE, out, err)
        kwargs = shared.subprocess.Popen.call_args.kwargs
        hook = kwargs.pop("preexec_fn")
        assert isinstance(hook, functools.partial) and hook.func is shared.child_limits
        assert hook.keywords == {"file_size_bytes": 1048576} and hook.args == ()
        env = {"QQ_WRAPPER_HOST_NONCE": "a" * 32, "ELECTRON_ENABLE_LOGGING": "1"}
        assert kwargs == dict(env=env, stdin=shared.subprocess.DEVNULL, stdout=out, stderr=err, close_fds=True)
        waiter.assert_called_once_with(observer, env, timeout=30, report=report,
                                       detailed_resources=True, wait_symbols=True, exit_status=True)
        assert report["measured_supervisor_file_size_bytes"] == {"soft": 1048576, "hard": 1048576}
        assert "resource_bounds_unchanged" not in report["stall_capture"]
        assert not report["experimental_resource_profile"]["all_samples_match_expected"]
        assert report["_private_fatal_capture"] == shared.private_fatal_envelope(out, err)


@pytest.mark.parametrize("limits", [(16384, 16384), (1048576, 2097152)])
def test_supervisor_fails_closed(monkeypatch, observer, limits):
    """Mismatch cannot cause any QQ launch or parent limit increase."""
    resource = mock.Mock()
    resource.getrlimit.return_value = limits
    monkeypatch.setitem(sys.modules, "resource", resource)
    monkeypatch.setitem(sys.modules, "qq_host_dispatch_control", resources.dispatch)
    with pytest.raises(shared.runtime.ProbeError, match="supervisor_file_size_profile_invalid"):
        resources.integration.invoke(MODE, io.BytesIO(), io.BytesIO())
    shared.subprocess.Popen.assert_not_called()
    resource.setrlimit.assert_not_called()


def test_capture_profile():
    """Full files can exceed16KiB; prefixes stay bounded and old capture schema stays untouched."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        out.write(b"x" * 32768)
        err.write(b"y" * 1048576)
        report = {}
        stall.capture_categories(out, err, report, detailed=True)
        assert report["stall_capture"]["resource_bounds_unchanged"]["file_size_bytes"] == 16384
        shared.fsize_resource_profile(report)
        capture = report["stall_capture"]
        assert capture["stdout_size_bytes"] == 32768 and capture["stderr_size_bytes"] == 1048576
        assert capture["stdout_capture_prefix_full"] and capture["stderr_prefix_truncated"]
        assert not capture["stdout_file_limit_reached"] and capture["stderr_file_limit_reached"]
        assert capture["prefix_capture"] and capture["capture_limit_bytes"] == 16384
        assert not capture["stderr_categories"]["file_size_limit"]
        assert len(shared.private_fatal_envelope(out, err)["stdout_base64"]) == 21848


def test_identity_staging_private(monkeypatch, tmp_path, imports):
    """Fixed synthetic identity/preimports/private consent all reuse existing implementations."""
    monkeypatch.setattr(fatal_tests, "MODE", MODE)
    fatal_tests.test_identity(monkeypatch, tmp_path, imports)
    fatal_tests.test_mode_gate(monkeypatch)


def test_cli(monkeypatch, tmp_path):
    """Exclusive public evidence and required private path precede execution."""
    monkeypatch.setattr(fatal_tests, "fatal", fsize)
    fatal_tests.test_exclusive_and_missing_path(tmp_path, monkeypatch)


@pytest.mark.parametrize("size", [16384, 32768, 1048576])
def test_private_readers(monkeypatch, tmp_path, size):
    """Larger files never expand the existing private reader or public payload."""
    fatal_tests.test_bounded_roundtrip(tmp_path, size, monkeypatch)


def test_full_envelope_bound():
    """Worst-case fixed sampler plus new measured profile still fits128KiB."""
    report = json.loads(fatal_tests.exits.ARTIFACT.read_text())
    sample = report["stall_observation"]["samples"][1]
    sample["main"]["limits"]["file_size_bytes"] = {"soft": 1048576, "hard": 1048576}
    sample["main"]["task_exit_codes"] = {"counts": {str(65535-i): 64 for i in range(8)}, "overflow_count": 64, "unavailable_count": 64}
    sample["main"]["main_wait"] = {"category": "symbol", "symbol": "z" * 96}
    sample["main"]["task_waits"] = {"categories": dict.fromkeys(stall.WAIT_CATEGORIES, 64), "symbols": {"symbol:" + str(i) + "z" * 95: 64 for i in range(8)}, "overflow_count": 64}
    report["stall_observation"]["samples"] = [sample] * 30
    shared.fsize_resource_profile(report)
    assert report["experimental_resource_profile"]["all_samples_match_expected"]
    report["_private_fatal_capture"] = shared.private_fatal_envelope(io.BytesIO(b"x" * 16384), io.BytesIO(b"x" * 16384))
    assert len(json.dumps(report).encode()) < 131072


def test_frozen_actual_fsize():
    """Read closed public evidence only; host readiness is not native binding or login."""
    if not fsize.ARTIFACT.exists():
        pytest.skip("actual capture requires parent GO")
    current = json.loads(fsize.ARTIFACT.read_text())
    baseline = json.loads(logging_tests.logging.ARTIFACT.read_text())
    assert current["status"] == "observed" and current["errors"] == []
    assert current["exit_code"] == 0 and current["completion"] == "application_exited"
    assert current["host_dispatch_observation_complete"] and all(current["dispatch_progress"]["flags"].values())
    assert current["elapsed_seconds"] == 1.28
    assert baseline["completion"] == "watchdog_30s" and baseline["exit_code"] == -9
    for key in ("authored_source_sha256", "synthetic_identity", "invocation", "bounds_seconds", "isolation"):
        assert current[key] == baseline[key]
    assert current["host_software_sha256_before"] == current["host_software_sha256_after"] == baseline["host_software_sha256_before"]
    profile = current["experimental_resource_profile"]
    assert profile["all_samples_match_expected"] and profile["limits_sample_count"] == profile["total_sample_count"] == 2
    assert current["measured_supervisor_file_size_bytes"] == {"soft": 1048576, "hard": 1048576}
    assert all(item["file_size_bytes"] == {"soft": 1048576, "hard": 1048576} for item in profile["measured_numeric_limits"])
    assert "resource_bounds_unchanged" not in current["stall_capture"]
    assert current["private_fatal_capture"]["stdout"]["captured_length"] == 156
    assert current["private_fatal_capture"]["stderr"]["captured_length"] == 818
    assert current["private_export"]["linux_owner_mode_verified"] and current["private_export"]["length"] == 2354
    assert "_private_fatal_capture" not in current and "base64" not in json.dumps(current)
    assert not any(current[key] for key in ("native_binding_verified", "native_host_binding_load_verified", "safe_to_login", "native_login_verified", "full_sendrecv_verified"))
