"""Strict vendor lifecycle projections and fixed-source security regression tests."""
import ast
import copy
import hashlib
import json
from pathlib import Path

import pytest

from tools import qq_ephemeral_host_entry as shared
from tools import qq_vendor_bootstrap_control as vendor

NONCE = "a" * 32


def record(stage="timer8s"):
    """Construct only public lifecycle facts, without fabricating native/login state."""
    return dict(schema_version=1, nonce=NONCE, stage=stage,
                runtime={"versions": {"node": "22.20.0", "electron": "39.0.0", "modules": "140", "napi": "10"}, "exec_path_matches": True},
                vendor_bootstrap_returned=stage != "before_vendor_bootstrap",
                ready_callback_fired=False, app_is_ready=False,
                timer0_fired=stage in ("timer0", "timer5s", "timer8s"),
                timer5s_fired=stage in ("timer5s", "timer8s"), timer8s_fired=stage == "timer8s")


def encoded(value):
    """Serialize exactly one bounded progress line."""
    return (json.dumps(value) + "\n").encode()


def test_fixed_delegation_and_exact_original(monkeypatch):
    """No arbitrary modes, scripts, vendor calls or source overrides are admitted."""
    calls = []
    def capture(root, **kwargs):
        """Capture fixed consent and mode without executing any software."""
        calls.append((root, kwargs))
        return {"status": "not_executed"}
    monkeypatch.setattr(shared, "observe", capture)
    vendor.observe("root", execute=True)
    assert calls == [("root", {"execute": True, "control_mode": "vendor_bootstrap"})]
    assert shared.fixed_sources("vendor_bootstrap") == {shared.MAIN: Path(shared.__file__).with_name("qq_vendor_bootstrap_entry.js")}
    assert vendor.validate_vendor_entry(vendor.VENDOR_ENTRY) == vendor.VENDOR_ENTRY
    assert len(vendor.VENDOR_ENTRY) == 56
    for raw in (vendor.VENDOR_ENTRY + b"\n", b"", vendor.VENDOR_ENTRY.replace(b"module", b"exports")):
        with pytest.raises(shared.runtime.ProbeError):
            vendor.validate_vendor_entry(raw)


@pytest.mark.parametrize("stage", sorted(vendor.STAGES))
def test_strict_stages(stage):
    """Accept only internally consistent exact lifecycle stages."""
    value = record(stage)
    if stage == "ready_callback":
        value.update(ready_callback_fired=True, app_is_ready=True)
    assert vendor.parse_progress(encoded(value), NONCE) == value


@pytest.mark.parametrize("patch", [{"app_is_ready": 1}, {"raw": "private"}, {"nonce": "wrong"}, {"schema_version": True}, {"ready_callback_fired": True}, {"stage": "login"}, {"timer8s_fired": False}])
def test_reject_malformed_progress(patch):
    """Reject payload expansion, identity confusion and impossible checkpoint facts."""
    value = record()
    value.update(patch)
    with pytest.raises(shared.runtime.ProbeError):
        vendor.parse_progress(encoded(value), NONCE)


def test_bounded_and_duplicate_json():
    """No duplicate interpretation, multi-line output or unbounded payload survives."""
    raw = encoded(record())
    for candidate in (raw + raw, raw[:-1], b"x" * (shared.LIMIT + 1), raw.replace(b'"schema_version": 1', b'"schema_version": 1, "schema_version": 1')):
        with pytest.raises((shared.runtime.ProbeError, ValueError)):
            vendor.parse_progress(candidate, NONCE)


def test_observation_never_claims_login_or_readiness():
    """Successful timers are observation only, not a claim of QQ or Electron readiness."""
    value = record()
    raw = ("QQ_VENDOR_BOOTSTRAP_" + NONCE + ":").encode() + encoded(value)
    assert vendor.parse_record(raw, NONCE) == value
    report = shared.base_report("vendor_bootstrap")
    vendor.project_report(report, value, 0)
    assert report["status"] == "observed"
    assert report["electron_ready_verified"] is False
    assert report["native_login_verified"] is False
    assert report["session_initialized"] is False
    assert report["independent_backend_ready"] is False
    value.update(ready_callback_fired=True, app_is_ready=True)
    vendor.project_report(report, value, -9)
    assert report["electron_ready_verified"] is True
    assert report["status"] == "failed"


def test_source_only_observes_then_exact_vendor_expression():
    """Retain same top-level module identity, fixed timers and fail-fast categories."""
    source = shared.fixed_sources("vendor_bootstrap")[shared.MAIN].read_text(encoding="utf-8")
    call = vendor.VENDOR_ENTRY.decode()
    boundary = source.index(call)
    assert source.count(call) == 1
    for marker in ("const fs = require('fs')", "const app = require('electron').app", "checkpoint('before_vendor_bootstrap')", "app.once('ready'", "}, 0);", "}, 5000);", "}, 8000);"):
        assert source.index(marker) < boundary
    assert source.index("checkpoint('after_vendor_bootstrap')") > boundary
    for forbidden in ("process.dlopen", "wrapper.node", "engine.init", "initialize(", "connect(", "getSession(", "getQRCode", "passwordLogin", "--no-sandbox", "ELECTRON_RUN_AS_NODE", "process.argv", "whenReady", "Proxy("):
        assert forbidden not in source
    assert "app.exit(0)" in source
    assert "process.exit(1)" in source
    shared_source = Path(shared.__file__).read_text(encoding="utf-8")
    # Check the actual guarded call structurally, not whitespace or tuple spelling.
    observe = next(node for node in ast.parse(shared_source).body
                   if isinstance(node, ast.FunctionDef) and node.name == 'observe_host')
    launch = next(node for node in ast.walk(observe) if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute) and node.func.attr == 'Popen')
    vendor_gate = next(node for node in observe.body if isinstance(node, ast.If)
                       and isinstance(node.test, ast.Compare)
                       and isinstance(node.test.left, ast.Name)
                       and node.test.left.id == 'control_mode'
                       and any(isinstance(value, ast.Constant) and value.value == 'vendor_bootstrap'
                               for value in ast.walk(node.test)))
    checks = [node for node in ast.walk(vendor_gate) if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute)
              and node.func.attr == 'verify_offline_network']
    assert len(checks) == 1 and checks[0].lineno < launch.lineno
    assert shared.QQ_TIMEOUT == 30 and shared.OUTER_TIMEOUT == 45


def test_fixed_fatal_categories():
    """Fatal errors never reveal exception content and cannot become successful results."""
    value = {"schema_version": 1, "nonce": NONCE, "category": "uncaught_exception"}
    assert vendor.parse_fatal(encoded(value), NONCE) == value
    for patch in ({"stack": "private"}, {"category": "unknown"}, {"schema_version": True}):
        altered = copy.deepcopy(value)
        altered.update(patch)
        with pytest.raises(shared.runtime.ProbeError):
            vendor.parse_fatal(encoded(altered), NONCE)


def test_actual_saved_evidence():
    """Verify the single actual run without running native software during tests."""
    path = Path(__file__).parents[1] / "docs" / "qq-vendor-bootstrap-evidence.json"
    if not path.exists():
        pytest.skip("single actual run not yet saved")
    evidence = json.loads(path.read_text(encoding="utf-8"))
    progress = evidence.get("vendor_progress")
    if progress:
        assert vendor.parse_progress(encoded(progress), progress["nonce"]) == progress
    actual = evidence["host_record"]
    if actual:
        vendor.parse_record(("QQ_VENDOR_BOOTSTRAP_" + actual["nonce"] + ":").encode() + encoded(actual), actual["nonce"])
    else:
        assert evidence["status"] == "failed"
    assert evidence["host_software_unchanged"] is True
    assert len(evidence["host_software_sha256_before"]) == 5
    assert evidence["host_software_sha256_before"] == evidence["host_software_sha256_after"]
    assert evidence["process_retired"] is True
    assert all(evidence["offline_network"].values())
    assert evidence["native_login_verified"] is False
    source = shared.fixed_sources("vendor_bootstrap")[shared.MAIN]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == evidence["authored_source_sha256"]
