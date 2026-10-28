"""Mock-only extended QQ startup gate checks; never launch the actual QQ binary."""

import json
import subprocess
from unittest.mock import Mock

import pytest

from tools import qq_isolated_runtime_probe as probe


@pytest.fixture
def inert_software(tmp_path):
    """Provide only ELF identifying bytes, not usable QQ software."""
    root = tmp_path / "QQ"
    root.mkdir()
    binary = root / "qq"
    binary.write_bytes(b"\x7fELF" + b"\0" * 60)
    binary.chmod(0o755)
    return root.resolve()


@pytest.mark.parametrize("options", [
    {}, {"private_display": True},
    {"private_display": True, "filesystem_mode": "chroot"},
    {"private_display": True, "filesystem_mode": "pivot-root", "native_load_control": True},
    {"private_display": True, "filesystem_mode": "pivot-root", "userns_control": True},
])
def test_extended_startup_refuses_unsafe_modes(monkeypatch, options):
    """Enforce private pivot-root display and no competing execution controls."""
    monkeypatch.setattr(probe.sys, "platform", "linux")
    forbidden = Mock(side_effect=AssertionError("no candidate or process access"))
    monkeypatch.setattr(probe, "validate_root", forbidden)
    assert probe.probe("not-read", extended_startup=True, **options)["status"] == "refused"
    forbidden.assert_not_called()


def test_extended_startup_default_never_executes(monkeypatch, inert_software):
    """Require separate --execute even after choosing the extended observation."""
    monkeypatch.setattr(probe.sys, "platform", "linux")
    runner = Mock(side_effect=AssertionError("execution forbidden"))
    monkeypatch.setattr(probe.subprocess, "run", runner)
    report = probe.probe(inert_software, filesystem_mode="pivot-root",
                         private_display=True, extended_startup=True)
    assert report["status"] == "not_executed"
    assert report["safe_to_login"] is False
    runner.assert_not_called()


def test_extended_startup_helper_boundary_and_nonreadiness(monkeypatch, inert_software):
    """Propagate exactly one fixed flag and preserve failure and safety booleans."""
    monkeypatch.setattr(probe.sys, "platform", "linux")
    monkeypatch.setattr(probe.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(probe.shutil, "which", lambda name, path=None: str(inert_software / name))
    monkeypatch.setattr(probe, "namespace_ids", lambda: {"net": "n1", "mnt": "m1",
                                                         "pid": "p1"})
    monkeypatch.setattr(probe.os, "readlink", lambda path: "i1")
    child = probe.base_report()
    child.update(status="failed", runtime_executed=True, errors=["runtime_timeout"],
                 startup_observation="qq_invoked_watchdog_at_30s", runtime_record_present=False)
    runner = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(child).encode()))
    monkeypatch.setattr(probe.subprocess, "run", runner)
    report = probe.probe(inert_software, execute=True, filesystem_mode="pivot-root",
                         private_display=True, extended_startup=True)
    assert runner.called, report
    args = runner.call_args.args[0]
    assert runner.call_args.kwargs["timeout"] == 35
    assert all(flag in args for flag in ("--net", "--mount", "--pid", "--ipc",
                                        "--kill-child", "--private-display", "--extended-startup"))
    assert "--no-sandbox" not in args
    assert report["startup_observation"] == "qq_invoked_watchdog_at_30s"
    assert report["runtime_record_present"] is False
    assert report["runtime_versions_verified"] is False
    assert report["native_binding_verified"] is False
    assert report["safe_to_login"] is False
    assert report["independent_backend_ready"] is False
