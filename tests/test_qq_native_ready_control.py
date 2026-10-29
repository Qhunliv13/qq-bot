"""Non-native readiness guards, exact projections and saved actual evidence checks."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from tools import qq_ephemeral_host_entry as shared
from tools import qq_native_connect_control as connect
from tools import qq_native_ready_control as ready
from test_qq_native_connect_control import NONCE, record


def encoded(value):
    """Encode the fixed ready envelope with one nonce-bound record."""
    return ("QQ_NATIVE_READY_" + NONCE + ":" + json.dumps(value) + "\n").encode()


def ready_record():
    """Reuse the established connect schema rather than creating another fixture ABI."""
    return dict(record(), readiness={"when_ready_resolved": True, "app_is_ready": True, "pure_timer_fired": True})


def test_fixed_ready_delegation(monkeypatch):
    """Only fixed authored ready source and shared offline isolation are available."""
    calls = []
    def capture(root, **kwargs):
        """Capture consent and fixed mode without executing any runtime."""
        calls.append((root, kwargs))
        return {"status": "not_executed"}
    monkeypatch.setattr(shared, "observe", capture)
    assert ready.observe("root", execute=True)["status"] == "not_executed"
    assert calls == [("root", {"execute": True, "control_mode": "connect_ready_offline"})]
    assert shared.fixed_sources("connect_ready_offline")[shared.MAIN].name == "qq_native_ready_entry.js"
    assert shared.base_report("connect_ready_offline")["electron_ready_verified"] is False


def test_ready_record_reuses_connect_validation():
    """Actual readiness cannot turn a transport observation into a login claim."""
    value = ready_record()
    assert ready.parse_record(encoded(value), NONCE) == value
    report = shared.base_report("connect_ready_offline")
    ready.project_report(report, value, 0)
    assert report["electron_ready_verified"] is True
    assert report["native_login_verified"] is False
    assert report["independent_backend_ready"] is False
    for patch in ({"app_is_ready": 1}, {"when_ready_resolved": False}, {"raw": "secret"}):
        altered = copy.deepcopy(value)
        altered["readiness"].update(patch)
        with pytest.raises(shared.runtime.ProbeError):
            ready.parse_record(encoded(altered), NONCE)


@pytest.mark.parametrize("stage", ready.STAGES)
def test_exact_ready_stage_extension(stage):
    """Only explicit ready stages extend the old parser; booleans stay exact."""
    status = None if stage in ready.STAGES[:3] else record()["initialization"]["snapshot"] if stage == "before_connect" else record()["connection"]["snapshot"]
    value = {"schema_version": 1, "nonce": NONCE, "stage": stage, "snapshot": status,
             "app_is_ready": stage != "before_when_ready", "pure_timer_fired": False}
    assert ready.parse_progress((json.dumps(value) + "\n").encode(), NONCE) == value
    with pytest.raises(shared.runtime.ProbeError):
        connect.parse_progress((json.dumps(value) + "\n").encode(), NONCE)
    value["app_is_ready"] = 1
    with pytest.raises(shared.runtime.ProbeError):
        ready.parse_progress((json.dumps(value) + "\n").encode(), NONCE)


def test_source_orders_ready_before_all_native_work():
    """Enforce the causal boundary and one-call/no-sensitive-operation source guards."""
    source = shared.fixed_sources("connect_ready_offline")[shared.MAIN].read_text(encoding="utf-8")
    boundary = source.index("await app.whenReady();")
    for marker in ("process.dlopen(", "require('/tmp/native_metadata.js')", "require('/tmp/native_login.js')", "controller.initialize();", "controller.connect();"):
        assert source.index(marker) > boundary
    assert source.index("setTimeout(() =>") < boundary
    assert source.count("controller.connect();") == 1
    for stage in ready.STAGES:
        assert "checkpoint('" + stage + "'" in source
    for forbidden in ("getQRCodePicture(", "getSession(", "passwordLogin(", "--no-sandbox", "process.argv", "ELECTRON_RUN_AS_NODE"):
        assert forbidden not in source
    assert "process.once('uncaughtException'" in source
    assert "process.once('unhandledRejection'" in source
    assert "app.exit(" in source
    assert shared.QQ_TIMEOUT == 30 and shared.OUTER_TIMEOUT == 45


def test_nonready_record_is_not_a_verified_native_result():
    """A parser-only refusal envelope never fabricates initialization or readiness."""
    value = ready_record()
    value.update(ok=False, error="ready_wait_failed")
    value["readiness"] = {"when_ready_resolved": False, "app_is_ready": False, "pure_timer_fired": True}
    value["binding"] = {"load_attempted": False, "loaded": False}
    value["initialization"] = {"attempted": False, "initialized": False, "snapshot": None, "listener_registered": False, "disposed": False}
    value["connection"].update(attempted=False, call_threw=False, wait_completed=False, snapshot=None, disposed_snapshot=None)
    assert ready.parse_record(encoded(value), NONCE) == value
    report = shared.base_report("connect_ready_offline")
    ready.project_report(report, value, 1)
    assert report["status"] == "failed"
    assert report["electron_ready_verified"] is False
    assert report["native_initialize_verified"] is False
    assert report["native_connect_observation_verified"] is False


def test_fatal_projection_has_no_payload():
    """Fail-fast handlers expose only a bounded nonce-bound event category."""
    value = {"schema_version": 1, "nonce": NONCE, "category": "uncaught_exception"}
    assert ready.parse_fatal((json.dumps(value) + "\n").encode(), NONCE) == value
    value["stack"] = "private"
    with pytest.raises(shared.runtime.ProbeError):
        ready.parse_fatal((json.dumps(value) + "\n").encode(), NONCE)


def test_actual_evidence_when_present():
    """Validate saved facts without performing a second native run."""
    path = Path(__file__).parents[1] / "docs" / "qq-native-ready-evidence.json"
    if not path.exists():
        pytest.skip("one authorized actual diagnostic has not yet been recorded")
    evidence = json.loads(path.read_text(encoding="utf-8"))
    progress = evidence.get("connect_progress")
    if progress:
        assert ready.parse_progress((json.dumps(progress) + "\n").encode(), progress["nonce"]) == progress
    actual = evidence["host_record"]
    if actual:
        raw = ("QQ_NATIVE_READY_" + actual["nonce"] + ":" + json.dumps(actual) + "\n").encode()
        assert ready.parse_record(raw, actual["nonce"]) == actual
    else:
        assert evidence["status"] == "failed"
        assert evidence["native_connect_observation_verified"] is False
    assert evidence["host_software_unchanged"] is True
    assert len(evidence["host_software_sha256_before"]) == 5
    assert evidence["host_software_sha256_before"] == evidence["host_software_sha256_after"]
    assert evidence["process_retired"] is True
    assert all(evidence["offline_network"].values())
    assert evidence["native_login_verified"] is False
    source = shared.fixed_sources("connect_ready_offline")[shared.MAIN]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == evidence["authored_source_sha256"]
    for relative in ("tools/qq_native_ready_control.py", "tools/qq_native_ready_entry.js", "tests/test_qq_native_ready_control.py", "docs/QQ_NATIVE_READY_CONTROL.md", "docs/qq-native-ready-evidence.json"):
        raw = (path.parents[1] / relative).read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf")
        raw.decode("utf-8")
