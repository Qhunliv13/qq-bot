"""Pin fixed preparation authorization, strict descriptors, and actual non-readiness."""
import copy
import json
from pathlib import Path

import pytest

from tools import qq_ephemeral_host_entry as shared
from tools import qq_native_factory_control as control


EVIDENCE = Path(__file__).parents[1] / "docs" / "qq-native-factory-evidence.json"


def actual():
    """Read only the checked-in redacted actual observation, never run QQ in tests."""
    return json.loads(EVIDENCE.read_text(encoding="utf-8-sig"))


def encode(value):
    """Encode the fixed marker using the record's actual nonce."""
    return ("QQ_NATIVE_FACTORY_" + value["nonce"] + ":" + json.dumps(value) + "\n").encode()


def test_fixed_mode_and_sources(monkeypatch):
    """Delegate isolation once and retain the existing reusable native modules."""
    calls = []
    def capture(root, **kwargs):
        """Record fixed delegation without executing a native host."""
        calls.append((root, kwargs))
        return {"status": "not_executed"}
    monkeypatch.setattr(shared, "observe", capture)
    assert control.observe("root", execute=True)["status"] == "not_executed"
    assert calls == [("root", {"execute": True, "control_mode": "initialize_factories"})]
    sources = shared.fixed_sources("initialize_factories")
    assert sources[shared.MAIN].name == "qq_native_factory_entry.js"
    assert set(sources) == {shared.MAIN, "/tmp/native_login.js", "/tmp/native_metadata.js"}
    assert shared.fixed_sources("initialize")[shared.MAIN].name == "qq_native_init_entry.js"


def test_actual_record_and_boundaries():
    """Returned factories plus config init are evidence, not login/session readiness."""
    report = actual()
    record = report["host_record"]
    assert control.parse_record(encode(record), record["nonce"]) == record
    progress = report["factory_progress"]
    assert control.parse_progress((json.dumps(progress) + "\n").encode(), record["nonce"]) == progress
    assert report["status"] == "observed" and report["exit_code"] == 0
    assert report["host_software_unchanged"] and report["host_software_sha256_before"] == report["host_software_sha256_after"]
    assert len(report["host_software_sha256_before"]) == 5
    assert report["native_initialize_verified"] and report["process_retired"]
    assert report["startup_factory_created"] and report["session_factory_available"]
    assert all(report["isolation"][key] for key in ("network", "mount", "pid", "ipc", "pivotRoot", "oldroot_detached", "unprivileged", "no_new_privileges", "effective_capabilities_zero"))
    assert all(report["offline_network"].values())
    for key in ("native_login_verified", "session_initialized", "full_sendrecv_verified", "safe_to_login", "independent_backend_ready", "production_cutover", "native_listener_registered", "native_listener_disposed"):
        assert report[key] is False
    snapshot = record["initialization"]["snapshot"]
    assert snapshot["connect_result_type"] == "not_attempted"
    assert not any(snapshot["native_callback_counts"].values())
    assert snapshot["listener_id_type"] == "undefined" and snapshot["cleanup_required"] == "process_exit"


@pytest.mark.parametrize("change", ["secret", "nonce", "stage", "type", "factory_bool", "validated", "early", "auth", "qr", "connect", "binding", "init", "raw_error", "dispose"])
def test_strict_record(change):
    """Reject disclosure, stage lies, and any authentication/connect claim."""
    record = copy.deepcopy(actual()["host_record"])
    nonce = record["nonce"]
    if change == "secret":
        record["factories"]["account"] = "forbidden"
    elif change == "nonce":
        record["nonce"] = "0" * 32
    elif change == "stage":
        record["factories"]["stage"] = "session_ready"
    elif change == "type":
        record["factories"]["startup_type"] = "string"
    elif change == "factory_bool":
        record["factories"]["session_factory_available"] = 1
    elif change == "validated":
        record["factories"]["validated"] = False
    elif change == "early":
        record["factories"]["stage"] = "before_session_get"
    elif change in ("auth", "qr"):
        record["initialization"]["snapshot"]["authenticated" if change == "auth" else "qr_available"] = True
    elif change == "connect":
        record["initialization"]["snapshot"].update(connect_call_returned=True, connect_result_type="boolean")
    elif change == "binding":
        record["binding"]["loaded"] = False
    elif change == "init":
        record["initialization"]["attempted"] = False
    elif change == "dispose":
        record["initialization"]["disposed"] = False
    else:
        record["error"] = "native private diagnostic"
    with pytest.raises(shared.runtime.ProbeError):
        control.parse_record(encode(record), nonce)


def test_progress_bounded_and_nonce_bound():
    """No raw message, duplicate field, repeated output, or wrong nonce is accepted."""
    report = actual()
    progress = report["factory_progress"]
    raw = (json.dumps(progress) + "\n").encode()
    for invalid in (raw + b"\n", raw + b"x" * shared.LIMIT,
                    raw.replace(b'"schema_version": 1', b'"schema_version": 1, "schema_version": 1')):
        with pytest.raises(shared.runtime.ProbeError):
            control.parse_progress(invalid, progress["nonce"])
    with pytest.raises(shared.runtime.ProbeError):
        control.parse_progress(raw, "0" * 32)


def test_source_order_and_forbidden_calls():
    """Factory order is observed, getters are cached, and no session/login starts."""
    source = shared.fixed_sources("initialize_factories")[shared.MAIN].read_text(encoding="utf-8")
    markers = ["const engine = engineGet.call(engineClass)", "const login = loginGet.call(loginClass)",
               "const startup = startupCreate.call(startupClass)", "const session = sessionGet.call(sessionClass, 'nt_1')", "controller.initialize();", "controller.dispose();"]
    assert [source.index(marker) for marker in markers] == sorted(source.index(marker) for marker in markers)
    for marker in ("before_engine_get", "after_engine_get", "before_login_get", "after_login_get", "before_startup_create", "after_startup_create", "before_session_get", "after_session_get", "before_initialize", "after_initialize"):
        assert "checkpoint('" + marker + "')" in source
    for forbidden in (".connect(", "session.init(", "session.start(", "startNT(", ".getQRCodePicture(", "process.argv", "new Proxy", "setTimeout", "--no-sandbox", "ELECTRON_RUN_AS_NODE"):
        assert forbidden not in source
    assert "get() { return engine; }" in source and "get() { return login; }" in source
    assert "appid: 537391664" in source and "V1_LNX_NQ_3.2.34_53644_GW_B" in source
