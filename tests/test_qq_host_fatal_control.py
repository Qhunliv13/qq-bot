"""Fake-only private-envelope boundaries and unchanged fatal observation conditions."""
import base64
import hashlib
import io
import json
import os
import sys
from unittest import mock

import pytest
import test_qq_host_resources_control as resource_tests
from tools import qq_ephemeral_host_entry as shared
from tools import qq_host_fatal_control as fatal
from tools import qq_host_exit_control as exits
from tools import qq_vendor_stall_observation as stall

observer = resource_tests.observer
imports = resource_tests.imports
MODE = "host_dispatch_fatal_offline"


@pytest.mark.parametrize("size", [0, 156, 16384, 16385, 32768])
def test_bounded_roundtrip(tmp_path, size, monkeypatch):
    """Preserve arbitrary bytes privately, never in the returned public report."""
    if os.name == "nt":
        monkeypatch.setattr(shared.runtime, "validate_diagnostic_fd", mock.Mock())
    raw = bytes(range(256)) * (size // 256) + b"x" * (size % 256)
    streams = [mock.Mock(wraps=io.BytesIO(raw)) for _ in range(2)]
    envelope = shared.private_fatal_envelope(*streams)
    for stream in streams:
        stream.read.assert_called_once_with(16384)
    report = {"_private_fatal_capture": envelope}
    path = tmp_path / "private.json"
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        shared.persist_private_fatal(report, fd)
    finally:
        os.close(fd)
    private = json.loads(path.read_text())
    assert base64.b64decode(private["stdout_base64"]) == raw[:16384]
    assert private["stderr"] == raw[:16384].decode("utf-8", errors="replace")
    assert set(report) == {"private_fatal_capture"}
    assert report["private_fatal_capture"]["stdout"] == {
        "captured_length": min(size, 16384), "sha256": hashlib.sha256(raw[:16384]).hexdigest()}


@pytest.mark.parametrize("payload", [None, {}, {"stdout_base64": "", "stderr_base64": "", "extra": "secret"},
    {"stdout_base64": "secret!", "stderr_base64": ""},
    {"stdout_base64": 5, "stderr_base64": ""},
    {"stdout_base64": base64.b64encode(b"x" * 16385).decode(), "stderr_base64": ""}])
def test_malformed_stripped(payload):
    """Invalid private input is removed before any validation exception can escape."""
    report = {"_private_fatal_capture": payload}
    with pytest.raises(shared.runtime.ProbeError, match="private_capture_invalid"):
        shared.persist_private_fatal(report, -1)
    assert report == {}


def test_exact_waiter(monkeypatch, observer):
    """The new mode changes no invocation, sampler, detailed capture, or source bytes."""
    monkeypatch.setitem(sys.modules, "qq_host_dispatch_control", resource_tests.dispatch)
    waiter, capture = mock.Mock(), mock.Mock()
    monkeypatch.setattr(stall, "observe_wait", waiter)
    monkeypatch.setattr(stall, "capture_categories", capture)
    out, err = io.BytesIO(b"synthetic-out"), io.BytesIO(b"synthetic-err")
    report = resource_tests.integration.invoke(MODE, out, err)
    waiter.assert_called_once_with(observer, {"QQ_WRAPPER_HOST_NONCE": "a" * 32}, timeout=30,
                                   report=report, detailed_resources=True, wait_symbols=True, exit_status=True)
    capture.assert_called_once_with(out, err, report, detailed=True)
    assert report["_private_fatal_capture"] == shared.private_fatal_envelope(out, err)
    assert shared.fixed_sources(MODE) == shared.fixed_sources("host_dispatch_exit_offline")


def test_source_digest():
    """Check authored source outside the observer fixture's synthetic filesystem."""
    assert hashlib.sha256(shared.fixed_sources(MODE)[shared.MAIN].read_bytes()).hexdigest() == "4ab8582d780a2cbf4a1fdddc90990dc0c74d594edc8f177dfe219b2b61993a94"


def test_identity(monkeypatch, tmp_path, imports):
    """Reuse identical identity gating and staged dependency tests without launching QQ."""
    monkeypatch.setattr(resource_tests.identity_tests, "MODE", MODE)
    resource_tests.identity_tests.test_child_identity_only_new_fixed_mode(monkeypatch, tmp_path, imports, MODE)
    resource_tests.identity_tests.test_exact_identity_gate(MODE)
    monkeypatch.setitem(resource_tests.integration.HELPERS, MODE, ("qq_host_dispatch_control", "qq_native_connect_control", "qq_application_observation", "qq_vendor_stall_observation"))
    resource_tests.integration.test_helpers_preimported_before_pivot(monkeypatch, tmp_path / "preimports", imports, MODE)


def test_exclusive_and_missing_path(tmp_path, monkeypatch):
    """Neither missing consent nor existing evidence can launch an experiment."""
    invoke = mock.Mock()
    monkeypatch.setattr(shared, "observe", invoke)
    with pytest.raises(SystemExit):
        fatal.main(["--qq-root", "unused", "--execute"])
    artifact = tmp_path / "evidence.json"
    artifact.write_text("preserved")
    monkeypatch.setattr(fatal, "ARTIFACT", artifact)
    with pytest.raises(FileExistsError):
        fatal.main(["--qq-root", "unused", "--execute", "--private-output", str(tmp_path / "private")])
    invoke.assert_not_called()


def test_mode_gate(monkeypatch):
    """Paths are accepted only by the fixed new mode, before any root or process access."""
    launch = mock.Mock()
    monkeypatch.setattr(shared.subprocess, "Popen", launch)
    assert shared.observe("unused", execute=True, private_capture_path="/tmp/private")["errors"] == ["private_capture_requires_fixed_fatal_mode"]
    assert shared.observe("unused", execute=True, control_mode=MODE)["errors"] == ["private_capture_path_required"]
    launch.assert_not_called()


def test_full_envelope_bound():
    """Maximal fixed exit observations plus both private prefixes fit unchanged128KiB."""
    report = json.loads(exits.ARTIFACT.read_text())
    main = report["stall_observation"]["samples"][1]["main"]
    # Real detailed modes aggregate threads; injecting raw threads creates an impossible schema.
    assert "threads" not in main and "thread_summary" in main
    main["task_exit_codes"] = {"counts": {str(65535-i): 64 for i in range(8)}, "overflow_count": 64, "unavailable_count": 64}
    main["main_wait"] = {"category": "symbol", "symbol": "z" * 96}
    main["task_waits"] = {"categories": dict.fromkeys(stall.WAIT_CATEGORIES, 64),
                          "symbols": {"symbol:" + str(i) + "z" * 95: 64 for i in range(8)}, "overflow_count": 64}
    report["stall_observation"]["samples"] = [report["stall_observation"]["samples"][1]] * 30
    report["_private_fatal_capture"] = shared.private_fatal_envelope(io.BytesIO(b"x" * 16384), io.BytesIO(b"x" * 16384))
    assert len(json.dumps(report).encode()) < shared.runtime.HELPER_OUTPUT_LIMIT


def test_frozen_actual_capture():
    """Verify public facts only; never read private application text in test output."""
    path = exits.ARTIFACT.with_name("qq-host-fatal-linux-evidence.json")
    if not path.exists():
        pytest.skip("actual capture requires explicit parent GO")
    current = json.loads(path.read_text())
    baseline = json.loads(exits.ARTIFACT.read_text())
    assert current["authored_source_sha256"] == baseline["authored_source_sha256"]
    assert current["synthetic_identity"] == baseline["synthetic_identity"]
    assert current["host_software_sha256_before"] == current["host_software_sha256_after"] == baseline["host_software_sha256_before"]
    samples = current["stall_observation"]["samples"]
    assert len(samples) == 30 and all(item["identity_verified"] for item in samples)
    assert [item["main"]["core_dumping"] for item in samples] == [0] + [1] * 29
    assert [item["main"]["exit_code"] for item in samples] == [0] + [5] * 29
    assert current["private_fatal_capture"]["stdout"]["captured_length"] == 156
    assert current["private_fatal_capture"]["stderr"]["captured_length"] == 555
    assert current["private_export"]["linux_owner_mode_verified"]
    assert current["completion"] == "watchdog_30s" and current["exit_code"] == -9
    assert "_private_fatal_capture" not in current and "base64" not in json.dumps(current)
    assert not current["safe_to_login"] and not current["native_binding_verified"]
