"""Identity-only safety gates and unchanged-client regression tests; no QQ runs."""

import hashlib
import inspect
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from tools import qq_bus_identity_control as identity
from tools import qq_isolated_runtime_probe as runtime


def gate(**changes):
    """Build the fixed gate without requiring any host-provided environment variable."""
    values = dict(synthetic_identity=True, control_mode="bus_identity_offline",
                  filesystem_mode="pivot-root", private_display=True,
                  private_runtime_services=True, extended_startup=True,
                  application_observer=identity.observe_bus, userns_control=False,
                  native_load_control=False)
    values.update(changes)
    return runtime.validate_synthetic_identity(**values)


@pytest.mark.parametrize("changes", [dict(synthetic_identity=1), dict(control_mode="wrapper"),
    dict(control_mode=None), dict(filesystem_mode="chroot"), dict(private_display=False),
    dict(private_runtime_services=False), dict(extended_startup=False),
    dict(application_observer=None), dict(userns_control=True), dict(native_load_control=True)])
def test_gate(changes):
    """Every missing isolation condition is rejected without filesystem setup."""
    with pytest.raises(runtime.ProbeError):
        gate(**changes)
    assert gate() is None


def test_gate_precedes_side_effects(monkeypatch):
    """A wrong mode fails before namespace reads, mounts or identity writes."""
    monkeypatch.setattr(runtime, "namespace_ids", lambda: pytest.fail("namespace read"))
    monkeypatch.setattr(runtime, "run_checked", lambda args: pytest.fail("mount"))
    with pytest.raises(runtime.ProbeError, match="synthetic_identity_requires"):
        runtime.child_probe(None, None, None, None, None, synthetic_identity=True)
    assert inspect.signature(runtime.child_probe).parameters["synthetic_identity"].default is False


def test_unchanged_capture_and_mode(monkeypatch):
    """Only the report label changes; exact detailed capture receives original arguments."""
    report = {}
    args = (["/opt/QQ/qq"], {}, None, None, 30, report)
    def observe(*received):
        """Verify delegation preserves the exact observer contract."""
        assert received == args
        report.update(mode="isolated_bus_admission_capture_control", application_invoked=False)
    monkeypatch.setattr(identity.capture, "observe_bus", observe)
    identity.observe_bus(*args)
    assert report == {"mode": "isolated_bus_identity_control", "application_invoked": False}
    monkeypatch.setattr(identity.capture.bus.control, "observe", lambda *args, **kwargs: kwargs)
    assert identity.observe("root") == {"execute": False, "control_mode": "bus_identity_offline"}


@pytest.mark.parametrize("mode", ["bus_identity_offline", "bus_admission_capture_offline", "bus_admission_offline"])
def test_no_qq_and_old_modes_false(monkeypatch, tmp_path, mode):
    """All bus modes bypass QQ; only the new fixed mode enables synthetic identity."""
    control = identity.capture.bus.control
    monkeypatch.setattr(runtime, "validate_root", lambda value: tmp_path)
    monkeypatch.setitem(sys.modules, "qq_bus_identity_control", identity)
    monkeypatch.setitem(sys.modules, "qq_bus_capture_control", identity.capture)
    monkeypatch.setitem(sys.modules, "qq_bus_admission_control", identity.capture.bus)
    assert control.fixed_sources(mode) == {}
    assert control.base_report(mode)["invocation"]["argv"] == []
    monkeypatch.setattr(control, "fixed_sources", lambda mode: pytest.fail("QQ source"))
    def child(*args, **kwargs):
        """Check the sole authority switch is derived from fixed mode, not environment."""
        assert kwargs["synthetic_identity"] is (mode == "bus_identity_offline")
        assert kwargs["control_mode"] == mode
        return kwargs
    monkeypatch.setattr(runtime, "child_probe", child)
    args = SimpleNamespace(qq_root="root", jail=str(tmp_path), nonce="a" * 32,
                           parent_ns="{}", mount_tool="mount", pivot_tool="pivot",
                           umount_tool="umount", control_mode=mode)
    control.child_main(args)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux root ownership/permissions contract")
def test_exact_records_and_no_overwrite(tmp_path, monkeypatch):
    """Create exact fixed bytes only; deny any host file read and repeated setup."""
    if runtime.os.geteuid() != 0:
        pytest.skip("root-only jail setup")
    tmp_path.chmod(0o700)
    jail = tmp_path / "root"
    jail.mkdir()
    original = Path.open
    def exclusive(path, mode="r", *args, **kwargs):
        """Identity creation can only exclusively open the two jail-local records."""
        assert mode == "xb" and path.parent == jail / "etc"
        return original(path, mode, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", exclusive)
        report = runtime.create_synthetic_identity(jail)
    expected = {"passwd": b"qqprobe:x:65534:65534:isolated runtime:/tmp/home:/usr/sbin/nologin\n",
                "group": b"qqprobe:x:65534:\n"}
    assert {path.name for path in (jail / "etc").iterdir()} == set(expected)
    for name, content in expected.items():
        assert (jail / "etc" / name).read_bytes() == content
        assert report["records"]["/etc/" + name] == dict(
            sha256=hashlib.sha256(content).hexdigest(), root_owned=True, mode_0444=True)
    with pytest.raises(runtime.ProbeError):
        runtime.create_synthetic_identity(jail)
    alias = tmp_path / "alias"
    alias.symlink_to(jail, target_is_directory=True)
    with pytest.raises(runtime.ProbeError):
        runtime.create_synthetic_identity(alias)
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "etc").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(runtime.ProbeError):
        runtime.create_synthetic_identity(empty)


def test_frozen_actual_comparison():
    """Pin combined-record causality while explicitly denying QQ/readiness conclusions."""
    root = Path(__file__).resolve().parents[1]
    baseline = json.loads((root / "docs/evidence/qq-bus-capture-evidence.json").read_text(encoding="utf-8"))
    current = json.loads((root / "docs/evidence/qq-bus-identity-evidence.json").read_text(encoding="utf-8"))
    assert current["mode"] == "isolated_bus_identity_control"
    assert current["status"] == "observed" and current["experiment_completed"]
    assert current["bounds_seconds"] == baseline["bounds_seconds"]
    assert current["bus_admission"]["auth"] == baseline["bus_admission"]["auth"] == "auth_ok"
    assert current["bus_admission"]["identity"] == baseline["bus_admission"]["identity"]
    for method in ("hello", "get_id"):
        assert baseline["bus_admission"][method]["exit_code"] == 1
        assert current["bus_admission"][method]["exit_code"] == 0
        assert not current["bus_admission"][method]["stderr"]["present"]
        assert not any(current["bus_admission"][method]["stderr"]["categories"].values())
    assert current["host_software_sha256_before"] == current["host_software_sha256_after"]
    assert current["host_software_sha256_before"] == baseline["host_software_sha256_before"]
    assert len(current["host_software_sha256_before"]) == 5
    assert current["synthetic_identity"]["enabled"]
    assert not current["synthetic_identity"]["host_identity_read"]
    assert set(current["synthetic_identity"]["records"]) == {"/etc/passwd", "/etc/group"}
    for key in ("application_invoked", "runtime_executed", "native_binding_verified",
                "runtime_versions_verified", "same_connection_stage_proof", "safe_to_login",
                "independent_backend_ready", "production_cutover", "production_cutover_authorized"):
        assert current[key] is False
    for name, artifact, expected in (
            ("qq_bus_admission_control.py", "qq-bus-admission-evidence.json", "2703501c713bcba9836bb0d074083ad4fda6557fbfd57350ceab66bf44d75f17"),
            ("qq_bus_capture_control.py", "qq-bus-capture-evidence.json", "c4dbd6a9aedc5544f0d975c8d7696801ecf390907242d56bc55be4a56442620b")):
        raw = (root / "tools" / name).read_bytes()
        # Only the reviewed artifact-directory relocation may differ from the frozen source.
        # Reconstruct that one historical literal; keep the original pinned digest so any
        # unrelated source change still fails and no historical byte-identity is claimed.
        relocated = f' / "docs" / "evidence" / "{artifact}"'.encode("utf-8")
        historical = f' / "docs" / "{artifact}"'.encode("utf-8")
        assert raw.count(relocated) == 1
        assert hashlib.sha256(raw.replace(relocated, historical, 1)).hexdigest() == expected
    assert identity.capture.bus.CALLBACK_LIMIT == 16 * 1024


def test_no_arbitrary_identity_cli():
    """The fixed CLI has no identity override or mode-selector option."""
    with pytest.raises(SystemExit):
        identity.main(["--qq-root", "unused", "--synthetic-identity"])
