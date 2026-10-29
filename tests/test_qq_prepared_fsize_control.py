"""Fixed prepared successor contracts using shared fake boundaries, never native launches."""
import functools
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from unittest import mock

import pytest
import test_qq_prepared_stall_integration as integration
import test_qq_prepared_connect_control as prepared_tests
import test_qq_host_identity_control as identity_tests
import test_qq_host_fatal_control as fatal_tests
from tools import qq_ephemeral_host_entry as shared
from tools import qq_prepared_connect_control as prepared
from tools import qq_prepared_fsize_control as fixed
from tools import qq_vendor_stall_observation as stall

imports = integration.imports
observer = integration.observer
MODE = "prepared_connect_fsize_offline"
OLD = "prepared_connect_offline"


def test_exact_sources():
    """Native entry and strict parser bytes are unchanged at the reviewed baseline."""
    assert shared.fixed_sources(MODE) == shared.fixed_sources(OLD)
    expected = {"tools/qq_prepared_connect_entry.js": "aead917c13298a0d5ee187524416fd929006790add64f33a4a8db18081676257",
                "tools/qq_prepared_connect_control.py": "2fff14f0d0a9a4aa0979c9dd99ca9ad2fd55eb6d6efd697858cb26ac30e5c0ff",
                "qq_transport/native_host.js": "81efd7b54dc0a8046687106fca60a617080f8a2bc0b550e396705fdd51a98112"}
    root = Path(__file__).resolve().parents[1]
    for name, digest in expected.items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
    assert set(shared.fixed_sources(MODE)) == {shared.MAIN, "/tmp/native_host.js", "/tmp/native_login.js", "/tmp/native_metadata.js"}


@pytest.mark.parametrize("mode", [OLD, MODE])
def test_exact_observer(monkeypatch, observer, mode):
    """Old kwargs stay exact; new route adds only reviewed wait/capture/resource options."""
    resource = mock.Mock()
    resource.getrlimit.return_value = (1048576, 1048576)
    monkeypatch.setitem(sys.modules, "resource", resource)
    waiter = mock.Mock()
    monkeypatch.setattr(stall, "observe_wait", waiter)
    capture = mock.Mock(wraps=stall.capture_categories)
    monkeypatch.setattr(stall, "capture_categories", capture)
    progress = mock.Mock(wraps=prepared.project_progress)
    monkeypatch.setattr(prepared, "project_progress", progress)
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        report = integration.invoke(mode, out, err)
        kwargs = shared.subprocess.Popen.call_args.kwargs
        hook = kwargs.pop("preexec_fn")
        env = {"QQ_WRAPPER_HOST_NONCE": "a" * 32}
        detail = {}
        if mode == MODE:
            env["ELECTRON_ENABLE_LOGGING"] = "1"
            detail = dict(detailed_resources=True, wait_symbols=True, exit_status=True)
            assert isinstance(hook, functools.partial) and hook.func is shared.child_limits
            assert hook.keywords == {"file_size_bytes": 1048576} and hook.args == ()
            capture.assert_called_once_with(out, err, report, detailed=True)
            assert report["_private_fatal_capture"] == shared.private_fatal_envelope(out, err)
            assert report["comparison_design"] == "successor_verified_host_profile"
        else:
            assert hook is shared.child_limits
            capture.assert_called_once_with(out, err, report)
            resource.getrlimit.assert_not_called()
        assert kwargs == dict(env=env, stdin=shared.subprocess.DEVNULL, stdout=out, stderr=err, close_fds=True)
        waiter.assert_called_once_with(observer, env, timeout=30, report=report, **detail)
        progress.assert_called_once_with(report, None)
        assert report["status"] == "failed" and report["host_record"] is None
        assert not report["native_login_verified"] and not report["session_initialized"]


def test_supervisor_mismatch(monkeypatch, observer):
    """No limit increase or launch follows a non-1MiB supervisor measurement."""
    resource = mock.Mock()
    resource.getrlimit.return_value = (16384, 16384)
    monkeypatch.setitem(sys.modules, "resource", resource)
    with pytest.raises(shared.runtime.ProbeError, match="supervisor_file_size_profile_invalid"):
        integration.invoke(MODE, io.BytesIO(), io.BytesIO())
    shared.subprocess.Popen.assert_not_called()
    resource.setrlimit.assert_not_called()


def test_identity_and_preimports(monkeypatch, tmp_path, imports):
    """Exact fully private identity gates and unchanged prepared helpers survive pivot."""
    monkeypatch.setattr(identity_tests, "MODE", MODE)
    identity_tests.test_exact_identity_gate(MODE)
    identity_tests.test_exact_identity_gate(OLD)
    identity_tests.test_child_identity_only_new_fixed_mode(monkeypatch, tmp_path / "identity", imports, MODE)
    monkeypatch.setitem(integration.HELPERS, MODE, integration.HELPERS[OLD])
    integration.test_helpers_preimported_before_pivot(monkeypatch, tmp_path / "imports", imports, MODE)


def test_cli_private_bounds(monkeypatch, tmp_path):
    """Exclusive artifacts, private consent and existing bounded exporter remain mandatory."""
    monkeypatch.setattr(fatal_tests, "fatal", fixed)
    fatal_tests.test_exclusive_and_missing_path(tmp_path, monkeypatch)
    fatal_tests.test_bounded_roundtrip(tmp_path, 1048576, monkeypatch)
    invoke = mock.Mock(return_value={"status": "not_executed"})
    monkeypatch.setattr(shared, "observe", invoke)
    fixed.observe("unused")
    invoke.assert_called_once_with("unused", execute=False, control_mode=MODE, private_capture_path=None)
    for option in ("--source", "--control-mode", "--file-size", "--synthetic-identity"):
        with pytest.raises(SystemExit):
            fixed.main(["--qq-root", "unused", option, "arbitrary"])


def test_exact_prepared_final_parser(tmp_path):
    """Real entry fake output passes the same parser; return true is not callback connected."""
    result = prepared_tests.fake_run(tmp_path)
    value = prepared.parse_record(result["raw"].encode(), prepared_tests.NONCE)
    report = shared.base_report(MODE)
    prepared.project_report(report, value, 0)
    assert report["status"] == "observed" and report["bounded_progress_verified"]
    assert value["connection"]["snapshot"]["phase"] == "connecting"
    assert value["connection"]["snapshot"]["native_callback_counts"]["onLoginConnected"] == 0
    assert not any(report[key] for key in ("safe_to_login", "native_login_verified", "session_initialized", "full_sendrecv_verified"))


def test_actual_evidence_when_frozen():
    """Validate closed evidence without assuming successful connectivity or retrying QQ."""
    if not fixed.ARTIFACT.exists():
        pytest.skip("one actual requires parent GO")
    current = json.loads(fixed.ARTIFACT.read_text())
    baseline = json.loads(fixed.ARTIFACT.with_name("qq-prepared-phase-evidence.json").read_text())
    assert current["authored_source_sha256"] == baseline["authored_source_sha256"]
    assert current["staged_source_sha256"] == baseline["staged_source_sha256"]
    assert current["host_software_sha256_before"] == current["host_software_sha256_after"] == baseline["host_software_sha256_before"]
    assert current["comparison_design"] == "successor_verified_host_profile"
    assert current["experimental_resource_profile"]["all_samples_match_expected"]
    assert current["private_export"]["linux_owner_mode_verified"]
    assert current["private_export"]["length"] <= 300 * 1024
    assert "_private_fatal_capture" not in current and "base64" not in json.dumps(current)
    assert not any(current[key] for key in ("safe_to_login", "native_login_verified", "session_initialized", "full_sendrecv_verified"))
    progress = current.get("connect_progress")
    if progress:
        assert prepared.parse_progress((json.dumps(progress) + "\n").encode(), progress["nonce"]) == progress
    record = current.get("host_record")
    if record:
        raw = ("QQ_PREPARED_CONNECT_" + record["nonce"] + ":" + json.dumps(record) + "\n").encode()
        assert prepared.parse_record(raw, record["nonce"]) == record
    # Exact closed observations describe this one run, never expected online readiness.
    assert current["status"] == "observed" and current["errors"] == []
    assert current["exit_code"] == 0 and current["completion"] == "application_exited"
    assert current["elapsed_seconds"] == 8.33
    assert current["bounded_progress_verified"] and current["native_connect_observation_verified"]
    assert progress["stage"] == "after_dispose"
    assert [item["elapsed_ms"] for item in progress["history"][-5:]] == [1045, 5046, 8045, 8045, 8045]
    assert record["connection"]["snapshot"]["phase"] == "disconnected"
    assert record["connection"]["snapshot"]["native_callback_counts"] == {
        "onLoginConnected": 0, "onLoginConnecting": 6, "onLoginDisConnected": 5,
        "onLoginFailed": 0, "onQRCodeGetPicture": 0, "onQRCodeLoginSucceed": 0,
        "onQRCodeSessionFailed": 0}
    assert current["experimental_resource_profile"]["limits_sample_count"] == 9
    assert current["private_export"]["length"] == 4504
