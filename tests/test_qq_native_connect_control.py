"""Non-native tests for fixed authorization, strict projections and offline bounds."""
import copy
import json
from pathlib import Path

import pytest

from tools import qq_ephemeral_host_entry as shared
from tools import qq_native_connect_control as control

NONCE = "c" * 32


def snapshot():
    """Model a measured ABI mismatch without treating it as native-call failure."""
    return {"phase": "failed", "disposed": False, "qr_available": False, "authenticated": False,
            "failure_code": "CONNECT_FAILED", "initialization_step": "initialized", "failed_stage": None,
            "registration_call_returned": True, "listener_id_type": "undefined", "listener_handle_available": False,
            "listener_registration_verified": False, "native_listener_removed": False, "cleanup_required": "process_exit",
            "connect_call_returned": True, "connect_result_type": "undefined", "listener_lifetime": "process",
            "native_callback_counts": {key: 0 for key in control.CALLBACK_NAMES}}


def record():
    """Build an observation record, not a claim of login or transport success."""
    status = snapshot()
    initialized = dict(copy.deepcopy(status), phase="initialized", failure_code=None, connect_call_returned=False, connect_result_type="not_attempted")
    disposed = dict(copy.deepcopy(status), phase="disconnected", disposed=True)
    return {"schema_version": 1, "nonce": NONCE, "ok": True, "error": None,
            "runtime": {"versions": {"node": "22.19.0", "electron": "39.0.0", "modules": "140", "napi": "10"}, "exec_path_matches": True},
            "binding": {"load_attempted": True, "loaded": True},
            "initialization": {"attempted": True, "initialized": True, "snapshot": initialized, "listener_registered": False, "disposed": True},
            "connection": {"attempted": True, "call_threw": True, "wait_completed": True, "wait_limit_ms": 8000,
                           "snapshot": status, "disposed_snapshot": disposed}}


def encoded(value):
    """Bind exactly one bounded JSON object to the fresh child nonce."""
    return ("QQ_NATIVE_CONNECT_" + NONCE + ":" + json.dumps(value) + "\n").encode()


def test_fixed_delegation(monkeypatch):
    """No alternate staging, source paths or native modes can be supplied."""
    calls = []
    def capture(root, **kwargs):
        """Capture delegation only; these tests never load a native library."""
        calls.append((root, kwargs))
        return {"status": "not_executed"}
    monkeypatch.setattr(shared, "observe", capture)
    assert control.observe("root", execute=True)["status"] == "not_executed"
    assert calls == [("root", {"execute": True, "control_mode": "connect_offline"})]
    sources = shared.fixed_sources("connect_offline")
    assert sources[shared.MAIN].name == "qq_native_connect_entry.js"
    assert set(sources) == {shared.MAIN, "/tmp/native_login.js", "/tmp/native_metadata.js"}


def test_undefined_is_observation_not_acceptance():
    """A strict-controller ABI rejection still preserves the actual returned type."""
    value = record()
    assert control.parse_record(encoded(value), NONCE) == value
    report = shared.base_report("connect_offline")
    control.project_report(report, value, 0)
    assert report["native_connect_observation_verified"] is True
    assert report["native_listener_registered"] is False
    assert report["native_cleanup_by_process_exit"] is True
    for key in ("native_login_verified", "session_initialized", "full_sendrecv_verified", "safe_to_login", "production_cutover", "independent_backend_ready"):
        assert report[key] is False


@pytest.mark.parametrize("kind", ["nonce", "extra", "raw", "count", "name", "qr", "auth", "wait", "call", "missing", "return", "proof", "duplicate", "suffix", "oversized"])
def test_rejects_unsafe_or_inconsistent_record(kind):
    """Reject private fields, made-up callback proof, unbounded output and lies."""
    value = copy.deepcopy(record())
    if kind == "nonce":
        value["nonce"] = "a" * 32
    elif kind == "extra":
        value["identity"] = "private"
    elif kind == "raw":
        value["connection"]["snapshot"]["raw_args"] = []
    elif kind == "count":
        value["connection"]["snapshot"]["native_callback_counts"]["onLoginConnected"] = True
    elif kind == "name":
        value["connection"]["snapshot"]["native_callback_counts"]["invented"] = 0
    elif kind == "qr":
        value["connection"]["snapshot"]["qr_available"] = True
    elif kind == "auth":
        value["connection"]["snapshot"]["authenticated"] = True
    elif kind == "wait":
        value["connection"]["wait_limit_ms"] = 8001
    elif kind == "call":
        value["connection"]["attempted"] = False
    elif kind == "missing":
        value["connection"]["snapshot"] = None
    elif kind == "return":
        value["connection"]["snapshot"]["connect_call_returned"] = False
    elif kind == "proof":
        value["connection"]["snapshot"]["listener_registration_verified"] = True
    raw = encoded(value)
    if kind == "duplicate":
        raw = raw.replace(b'"ok": true', b'"ok": false, "ok": true')
    elif kind == "suffix":
        raw += b"\n"
    elif kind == "oversized":
        raw += b"x" * shared.LIMIT
    with pytest.raises(shared.runtime.ProbeError):
        control.parse_record(raw, NONCE)


def test_actual_callback_proves_delivery_even_after_abi_rejection():
    """Counts report native invocations, not controller-generated connecting events."""
    value = record()
    for key in ("snapshot", "disposed_snapshot"):
        value["connection"][key]["native_callback_counts"]["onLoginConnecting"] = 1
        value["connection"][key]["listener_registration_verified"] = True
    assert control.parse_record(encoded(value), NONCE) == value


def test_entry_one_call_and_no_sensitive_operations():
    """The fixed source never invokes QR/password/session APIs or native teardown."""
    source = shared.fixed_sources("connect_offline")[shared.MAIN].read_text(encoding="utf-8")
    assert source.count("controller.connect();") == 1
    for expected in ("controller.initialize();", "controller.dispose();", "listenerLifetime: 'process'", "8000", "controller.getSnapshot()"):
        assert expected in source
    for forbidden in (".getQRCodePicture(", "getSession(", "passwordLogin(", "NODE_OPTIONS", "--no-sandbox", "process.argv"):
        assert forbidden not in source


@pytest.mark.parametrize("stage", ["before_initialize", "before_connect", "after_connect", "after_wait"])
def test_fixed_checkpoints(stage):
    """The bounded nonce checkpoint reports only actual fixed stages and safe status."""
    status = None if stage == "before_initialize" else record()["initialization"]["snapshot"] if stage == "before_connect" else snapshot()
    value = {"schema_version": 1, "nonce": NONCE, "stage": stage, "snapshot": status}
    assert control.parse_progress((json.dumps(value) + "\n").encode(), NONCE) == value
    value["private"] = "forbidden"
    with pytest.raises(shared.runtime.ProbeError):
        control.parse_progress((json.dumps(value) + "\n").encode(), NONCE)


def test_checked_in_evidence_when_present():
    """Apply live strict parsers to saved actual outcomes without rerunning native."""
    path = Path(__file__).parents[1] / "docs" / "evidence" / "qq-native-connect-evidence.json"
    if not path.exists():
        pytest.skip("one authorized actual observation not yet performed")
    evidence = json.loads(path.read_text(encoding="utf-8"))
    actual = evidence["host_record"]
    if actual is not None:
        raw = ("QQ_NATIVE_CONNECT_" + actual["nonce"] + ":" + json.dumps(actual) + "\n").encode()
        assert control.parse_record(raw, actual["nonce"]) == actual
    else:
        assert evidence["status"] == "failed"
        assert evidence["native_connect_observation_verified"] is False
        assert evidence["completion"] == "watchdog_30s"
        progress = evidence.get("connect_progress")
        if progress is not None:
            assert control.parse_progress((json.dumps(progress) + "\n").encode(), progress["nonce"]) == progress
    assert evidence["host_software_unchanged"] is True
    assert len(evidence["host_software_sha256_before"]) == 5
    assert evidence["host_software_sha256_before"] == evidence["host_software_sha256_after"]
    assert evidence["process_retired"] is True
    assert evidence["native_login_verified"] is False
    assert all(evidence["offline_network"].values())
    assert all(evidence["isolation"][key] for key in ("network", "mount", "pid", "ipc", "pivotRoot", "oldroot_detached", "unprivileged", "no_new_privileges", "effective_capabilities_zero"))
