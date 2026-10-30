"""Fixed native-free host identity routing and fair-contrast tests; no actual QQ run."""
import hashlib
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest import mock

import pytest
import test_qq_prepared_stall_integration as integration
from tools import qq_ephemeral_host_entry as shared
from tools import qq_host_dispatch_control as dispatch
from tools import qq_host_identity_control as identity
from tools import qq_isolated_runtime_probe as runtime
from tools import qq_vendor_stall_observation as stall

imports = integration.imports
observer = integration.observer
MODE = "host_dispatch_identity_offline"
BASELINE = "host_dispatch_capture_offline"
HELPERS = ("qq_host_dispatch_control", "qq_native_connect_control",
           "qq_application_observation", "qq_vendor_stall_observation")
SOURCE_HASH = "4ab8582d780a2cbf4a1fdddc90990dc0c74d594edc8f177dfe219b2b61993a94"


def test_fixed_sources_cli_and_exclusive_evidence(tmp_path, monkeypatch):
    """Pin source bytes and refuse arbitrary identities, sources, modes and overwrites."""
    assert shared.fixed_sources(MODE) == shared.fixed_sources(BASELINE)
    assert set(shared.fixed_sources(MODE)) == {shared.MAIN}
    assert hashlib.sha256(shared.fixed_sources(MODE)[shared.MAIN].read_bytes()).hexdigest() == SOURCE_HASH
    assert (shared.QQ_TIMEOUT, shared.OUTER_TIMEOUT, shared.LIMIT) == (30, 45, 16384)
    assert identity.ARTIFACT.name == "qq-host-identity-evidence.json"
    invoke = mock.Mock(return_value={"status": "not_executed"})
    monkeypatch.setattr(shared, "observe", invoke)
    identity.observe("unused")
    invoke.assert_called_once_with("unused", execute=False, control_mode=MODE)
    artifact = tmp_path / "existing.json"
    artifact.write_text("preserved", encoding="utf-8")
    monkeypatch.setattr(identity, "ARTIFACT", artifact)
    invoke.reset_mock()
    with pytest.raises(FileExistsError):
        identity.main(["--qq-root", "unused", "--execute"])
    assert artifact.read_text(encoding="utf-8") == "preserved"
    for option in ("--synthetic-identity", "--control-mode", "--source", "--output", "--passwd", "--group"):
        with pytest.raises(SystemExit):
            identity.main(["--qq-root", "unused", option, "arbitrary"])
    invoke.assert_not_called()


@pytest.mark.parametrize("mode", [MODE, BASELINE, "host_dispatch_offline", "host_dispatch_roles_offline", *integration.MODES, "prepared_phase_offline"])
def test_child_identity_only_new_fixed_mode(monkeypatch, tmp_path, imports, mode):
    """Old host calls keep their exact keyword set; only the fixed new host adds identity."""
    monkeypatch.setitem(sys.modules, "qq_host_dispatch_control", dispatch)
    from tools import qq_prepared_phase_control as phase
    monkeypatch.setitem(sys.modules, "qq_prepared_phase_control", phase)
    app = tmp_path / "resources/app"
    (app / "app_launcher").mkdir(parents=True)
    (app / "package.json").write_bytes(b"manifest")
    (app / "app_launcher/index.js").write_bytes(b"vendor")
    monkeypatch.setattr(runtime, "validate_root", lambda _value: tmp_path)
    monkeypatch.setattr(shared, "validate_manifest", lambda _value: b"control")
    monkeypatch.setattr(integration.vendor, "validate_vendor_entry", mock.Mock())
    child = mock.Mock(return_value={})
    monkeypatch.setattr(runtime, "child_probe", child)
    args = SimpleNamespace(qq_root="root", jail=str(tmp_path), nonce="a" * 32,
                           parent_ns="{}", mount_tool="mount", pivot_tool="pivot",
                           umount_tool="umount", control_mode=mode)
    shared.child_main(args)
    options = child.call_args.kwargs
    expected = dict(filesystem_mode="pivot-root", pivot_tool="pivot", umount_tool="umount",
                    private_display=True, extended_startup=True, private_runtime_services=True,
                    application_observer=options["application_observer"])
    if mode == MODE:
        expected.update(synthetic_identity=True, control_mode=MODE)
    assert options == expected


@pytest.mark.parametrize("mode", [MODE, "bus_identity_offline", BASELINE, "wrapper", "unknown", None])
def test_exact_identity_gate(mode):
    """Only the two fixed modes qualify, and every full-private condition remains mandatory."""
    options = dict(synthetic_identity=True, control_mode=mode, filesystem_mode="pivot-root",
                   private_display=True, private_runtime_services=True, extended_startup=True,
                   application_observer=identity.observe, userns_control=False, native_load_control=False)
    if mode not in (MODE, "bus_identity_offline"):
        with pytest.raises(runtime.ProbeError):
            runtime.validate_synthetic_identity(**options)
        return
    assert runtime.validate_synthetic_identity(**options) is None
    for name, value in (("synthetic_identity", 1), ("filesystem_mode", "chroot"),
                        ("private_display", False), ("private_runtime_services", False),
                        ("extended_startup", False), ("application_observer", None),
                        ("userns_control", True), ("native_load_control", True)):
        with pytest.raises(runtime.ProbeError):
            runtime.validate_synthetic_identity(**dict(options, **{name: value}))


def test_unknown_host_mode_rejected():
    """Unknown host modes cannot select source bytes or create reports."""
    for operation in (shared.validate_mode, shared.fixed_sources, shared.base_report):
        with pytest.raises(runtime.ProbeError):
            operation("host_identity_arbitrary")


def test_exact_capture_parser_waiter(monkeypatch, observer):
    """New host mode uses identical count-only waiter, detailed capture, parser and argv."""
    monkeypatch.setitem(sys.modules, "qq_host_dispatch_control", dispatch)
    waiter, capture = mock.Mock(), mock.Mock()
    monkeypatch.setattr(stall, "observe_wait", waiter)
    monkeypatch.setattr(stall, "capture_categories", capture)
    project = mock.Mock(wraps=dispatch.project_report)
    monkeypatch.setattr(dispatch, "project_report", project)
    out, err = io.BytesIO(), io.BytesIO()
    report = integration.invoke(MODE, out, err)
    waiter.assert_called_once_with(observer, {"QQ_WRAPPER_HOST_NONCE": "a" * 32}, timeout=30, report=report)
    capture.assert_called_once_with(out, err, report, detailed=True)
    project.assert_called_once_with(report, None, -9)
    assert report["mode"] == "ephemeral_" + MODE
    assert report["private_output_categories"] == {}
    assert shared.subprocess.Popen.call_args.args == (["/opt/QQ/qq"],)
    assert shared.stage_control.call_args.kwargs["auxiliary"] is None


def test_same_preimports_and_copydeps(monkeypatch, tmp_path, imports):
    """Copy exactly the old four helpers, with no bus client or extra bootstrap."""
    monkeypatch.setitem(sys.modules, "qq_host_dispatch_control", dispatch)
    monkeypatch.setitem(integration.HELPERS, MODE, HELPERS)
    integration.test_helpers_preimported_before_pivot(monkeypatch, tmp_path, imports, MODE)
    integration.test_outer_copy_set_matches_preimports(monkeypatch, tmp_path / "outer", MODE)


def test_actual_comparison_when_frozen():
    """Validate fair contrast without manufacturing future readiness or timer outcomes."""
    docs = Path(__file__).resolve().parents[1] / "docs" / "evidence"
    artifact = docs / "qq-host-identity-evidence.json"
    if not artifact.exists():
        pytest.skip("one actual run awaits parent review/GO")
    current = json.loads(artifact.read_text(encoding="utf-8"))
    baseline = json.loads((docs / "qq-host-dispatch-capture-evidence.json").read_text(encoding="utf-8"))
    assert current["mode"] == "ephemeral_" + MODE
    assert current["authored_source_sha256"] == baseline["authored_source_sha256"] == SOURCE_HASH
    assert current["bounds_seconds"] == baseline["bounds_seconds"] == {"qq": 30, "outer": 45}
    assert current["invocation"] == baseline["invocation"]
    assert current["host_software_sha256_before"] == current["host_software_sha256_after"] == baseline["host_software_sha256_before"]
    assert current["host_software_unchanged"] and current["experiment_completed"]
    assert current["synthetic_identity"]["enabled"] and not current["synthetic_identity"]["host_identity_read"]
    assert set(current["synthetic_identity"]["records"]) == {"/etc/passwd", "/etc/group"}
    assert "staged_source_sha256" not in current and "bus_admission" not in current
    assert current["stall_capture"]["resource_bounds_unchanged"] == baseline["stall_capture"]["resource_bounds_unchanged"]
    assert not current["stall_observation"]["window_roles_queried"]
    progress = current["dispatch_progress"]
    assert dispatch.parse_progress((json.dumps(progress) + "\n").encode(), progress["nonce"]) == progress
    for key in ("native_binding_verified", "native_host_binding_load_verified", "native_login_verified",
                "session_initialized", "full_sendrecv_verified", "safe_to_login", "independent_backend_ready",
                "production_cutover", "production_cutover_authorized"):
        assert current[key] is False
