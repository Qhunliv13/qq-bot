"""Verify fixed initialize-only authorization, strict records, and runner reuse."""
import copy
import json
from pathlib import Path

import pytest

from tools import qq_ephemeral_host_entry as shared
from tools import qq_native_init_control as control

NONCE = "a" * 32


def record():
    """Return only the safe schema for a completed initialize/remove cycle."""
    return {"schema_version": 1, "nonce": NONCE, "ok": True, "error": None,
            "runtime": {"versions": {"node": "24.11.1", "electron": "40.0.0", "modules": "143", "napi": "10"},
                        "exec_path_matches": True},
            "binding": {"load_attempted": True, "loaded": True},
            "initialization": {"attempted": True, "initialized": True, "listener_registered": True,
                               "disposed": True, "snapshot": {"phase": "initialized", "disposed": False,
                               "qr_available": False, "authenticated": False, "failure_code": None,
                               "initialization_step": "initialized", "failed_stage": None,
                               "registration_call_returned": True, "listener_id_type": "number",
                               "listener_handle_available": True, "listener_registration_verified": True,
                               "native_listener_removed": False, "cleanup_required": "remove_listener"}}}


def encoded(value):
    """Use only the nonce-bound initialize marker, not the wrapper marker."""
    return ("QQ_NATIVE_INIT_" + NONCE + ":" + json.dumps(value) + "\n").encode()


def test_observe_delegates_only_fixed_mode(monkeypatch):
    """The new control cannot implement another runner or inject source paths."""
    calls = []
    def capture(root, **kwargs):
        """Capture the trusted Python-only delegation without execution."""
        calls.append((root, kwargs))
        return {"status": "not_executed"}
    monkeypatch.setattr(shared, "observe", capture)
    assert control.observe("root", execute=True)["status"] == "not_executed"
    assert calls == [("root", {"execute": True, "control_mode": "initialize"})]


@pytest.mark.parametrize("mode", ["other", "../evil.js", "", None, True])
def test_modes_reject_arbitrary_sources(mode):
    """Only the enum can select source bytes before isolated execution."""
    with pytest.raises(shared.runtime.ProbeError, match="control_mode_invalid"):
        shared.fixed_sources(mode)


def test_sources_are_fixed_and_reusable():
    """Stage real reusable modules, never a loader or caller supplied substitute."""
    sources = shared.fixed_sources("initialize")
    assert set(sources) == {shared.MAIN, "/tmp/native_login.js", "/tmp/native_metadata.js"}
    assert sources[shared.MAIN].name == "qq_native_init_entry.js"
    assert sources["/tmp/native_login.js"].parent.name == "qq_transport"
    assert shared.fixed_sources("wrapper") == {shared.MAIN: shared.SOURCE}


def test_initialize_record_roundtrip():
    """Success means actual initialized snapshot plus actual successful disposal."""
    assert shared.parse_initialize_record(encoded(record()), NONCE) == record()


@pytest.mark.parametrize("change", ["nonce", "extra", "secret", "schema", "version", "auth", "qr", "dispose", "listener", "binding", "snapshot", "error"])
def test_strict_initialize_record(change):
    """No credentials, identity, QR, inconsistent success or raw error may escape."""
    value = copy.deepcopy(record())
    if change == "nonce":
        value["nonce"] = "b" * 32
    elif change == "extra":
        value["credentials"] = "not allowed"
    elif change == "secret":
        value["initialization"]["snapshot"]["identity"] = "not allowed"
    elif change == "schema":
        value["schema_version"] = True
    elif change == "version":
        value["runtime"]["versions"]["unknown"] = "no"
    elif change == "auth":
        value["initialization"]["snapshot"]["authenticated"] = True
    elif change == "qr":
        value["initialization"]["snapshot"]["qr_available"] = True
    elif change == "dispose":
        value["initialization"]["disposed"] = False
    elif change == "listener":
        value["initialization"]["listener_registered"] = False
    elif change == "binding":
        value["binding"]["loaded"] = False
    elif change == "snapshot":
        value["initialization"]["snapshot"] = None
    else:
        value["error"] = "native private diagnostic"
    with pytest.raises(shared.runtime.ProbeError):
        shared.parse_initialize_record(encoded(value), NONCE)


def test_failure_preserves_truthful_milestone():
    """A native initialize exception remains failed rather than inferred success."""
    value = record()
    value.update(ok=False, error="initialize_failed")
    value["initialization"].update(initialized=False, listener_registered=False, disposed=False)
    value["initialization"]["snapshot"].update(phase="failed", failure_code="INITIALIZE_FAILED",
                                               initialization_step="register_listener", failed_stage="register_listener",
                                               listener_registration_verified=False)
    assert shared.parse_initialize_record(encoded(value), NONCE) == value


@pytest.mark.parametrize("suffix", [b"extra", b"\n", b"x" * shared.LIMIT])
def test_record_bounded_single_output(suffix):
    """Repeated or oversized records cannot bypass the one fixed output surface."""
    with pytest.raises(shared.runtime.ProbeError):
        shared.parse_initialize_record(encoded(record()) + suffix, NONCE)


def test_duplicate_keys_rejected():
    """Duplicate keys cannot create two interpretations of native status."""
    raw = encoded(record()).replace(b'"ok": true', b'"ok": false, "ok": true')
    with pytest.raises(shared.runtime.ProbeError):
        shared.parse_initialize_record(raw, NONCE)


def test_fixed_entry_has_no_login_or_session_calls():
    """Only initialize, safe snapshot, listener disposal and public exit are called."""
    source = Path(shared.fixed_sources("initialize")[shared.MAIN]).read_text(encoding="utf-8")
    for expected in ("controller.initialize();", "controller.getSnapshot()", "controller.dispose();", "appid: 537391664",
                     "V1_LNX_NQ_3.2.34_53644_GW_B", "osRelease: os.release()", "hostname: 'qq-isolated-lab'"):
        assert expected in source
    for forbidden in (".connect(", ".getQRCodePicture(", "getSession(", "password", "process.argv", "NODE_OPTIONS", "--no-sandbox"):
        assert forbidden not in source


def test_checked_in_actual_evidence_is_strict_and_nonready():
    """The actual isolated failure record must meet the same parser as live output."""
    evidence_path = Path(__file__).parents[1] / "docs" / "qq-native-init-evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8-sig"))
    actual = evidence["host_record"]
    raw = ("QQ_NATIVE_INIT_" + actual["nonce"] + ":" + json.dumps(actual) + "\n").encode()
    assert shared.parse_initialize_record(raw, actual["nonce"]) == actual
    assert evidence["host_software_unchanged"] is True
    assert evidence["host_software_sha256_before"] == evidence["host_software_sha256_after"]
    assert evidence["native_initialize_verified"] is True
    assert evidence["native_listener_registered"] is False
    assert evidence["native_listener_disposed"] is False
    assert evidence["native_cleanup_by_process_exit"] is True
    assert evidence["process_retired"] is True
    assert evidence["native_login_verified"] is False
    assert actual["initialization"]["snapshot"]["listener_id_type"] == "undefined"
    assert actual["initialization"]["snapshot"]["registration_call_returned"] is True
    assert all(evidence["isolation"][key] for key in ("network", "mount", "pid", "ipc", "pivotRoot", "oldroot_detached", "unprivileged", "no_new_privileges", "effective_capabilities_zero"))


def test_readiness_does_not_escalate():
    """Native initialization is not authentication, transport, or cutover readiness."""
    report = shared.base_report("initialize")
    assert report["mode"] == "ephemeral_native_init"
    for key in ("native_initialize_verified", "native_login_verified", "session_initialized", "full_sendrecv_verified",
                "safe_to_login", "independent_backend_ready", "production_cutover"):
        assert report[key] is False
