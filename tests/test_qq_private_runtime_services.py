"""Mock-only checks for private services; these tests never run QQ or namespaces."""

import json
import subprocess
from unittest.mock import Mock

import pytest

from tools import qq_isolated_runtime_probe as probe


@pytest.mark.parametrize("options,expected", [
    ({"private_runtime_services": True}, "private_runtime_services_require_private_display"),
    ({"private_runtime_services": True, "private_display": True}, "private_display_requires_pivot_runtime"),
    ({"private_runtime_services": True, "private_display": True,
      "filesystem_mode": "pivot-root", "userns_control": True}, "private_display_requires_pivot_runtime"),
    ({"private_runtime_services": True, "private_display": True,
      "filesystem_mode": "pivot-root", "native_load_control": True}, "private_display_requires_pivot_runtime"),
])
def test_service_gate_refuses_unsafe_modes(options, expected, monkeypatch):
    """Reject unsafe combinations before any file or process access."""
    monkeypatch.setattr(probe.sys, "platform", "linux")
    monkeypatch.setattr(probe, "validate_root", Mock(side_effect=AssertionError("must not read")))
    monkeypatch.setattr(probe.subprocess, "run", Mock(side_effect=AssertionError("must not execute")))
    assert probe.probe("invalid", **options)["errors"] == [expected]


def test_service_mode_needs_separate_execute(monkeypatch, tmp_path):
    """Selecting all three modes without consent cannot start even the helper."""
    monkeypatch.setattr(probe.sys, "platform", "linux")
    root = tmp_path / "QQ"
    root.mkdir()
    qq = root / "qq"
    qq.write_bytes(b"\x7fELF")
    qq.chmod(0o755)
    runner = Mock(side_effect=AssertionError("must not execute"))
    monkeypatch.setattr(probe.subprocess, "run", runner)
    report = probe.probe(root.resolve(), filesystem_mode="pivot-root", private_display=True,
                          private_runtime_services=True)
    assert report["status"] == "not_executed"
    assert report["runtime_executed"] is False
    runner.assert_not_called()


def test_private_bus_configuration_has_no_activation_or_host_path():
    """Keep the generated bus isolated rather than importing host service policy."""
    text = probe.private_bus_configuration()
    assert "unix:path=/tmp/runtime/bus" in text
    assert "EXTERNAL" in text
    for forbidden in ("servicedir", "standard_session", "/run/user", "/home", "/etc/dbus", "include"):
        assert forbidden not in text


def test_window_counter_redacts_titles(monkeypatch, tmp_path):
    """Only a bounded number of X root children leaves the private observation."""
    monkeypatch.setattr(probe, "Path", lambda path: tmp_path / "count")

    def fake_run(args, **kwargs):
        """Write fake xwininfo output into the dedicated capture file."""
        assert args[:2] == ["/usr/bin/xwininfo", "-root"]
        kwargs["stdout"].write(b"3 children:\n  0x1234 secret-window-title\n")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    assert probe.private_window_count({"DISPLAY": ":99"}) == 3


def test_parent_accepts_only_bounded_service_and_window_fields(monkeypatch, tmp_path):
    """Never forward injected paths, account tokens, or affirmative readiness."""
    monkeypatch.setattr(probe.sys, "platform", "linux")
    root = tmp_path / "QQ"
    root.mkdir()
    qq = root / "qq"
    qq.write_bytes(b"\x7fELF")
    qq.chmod(0o755)
    monkeypatch.setattr(probe.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(probe.shutil, "which", lambda name, **kw: str(root / name))
    monkeypatch.setattr(probe, "validate_root", lambda candidate: root.resolve())
    monkeypatch.setattr(probe.os, "readlink", lambda path: "ipc-parent")
    monkeypatch.setattr(probe, "namespace_ids", lambda: {k: k for k in probe.NAMESPACES})
    child = probe.base_report()
    child.update(status="failed", errors=["runtime_timeout"], runtime_executed=True,
                 private_runtime_services={"bus_ready": True, "account": "secret"},
                 private_window_observation={"sampled": True, "baseline_children": 0,
                                             "maximum_children": 2, "title": "secret"},
                 native_binding_verified=True)
    runner = Mock(return_value=subprocess.CompletedProcess([], 1, json.dumps(child).encode()))
    monkeypatch.setattr(probe.subprocess, "run", runner)
    result = probe.probe(root.resolve(), execute=True, filesystem_mode="pivot-root",
                          private_display=True, private_runtime_services=True)
    assert runner.called, result["errors"]
    assert "--private-runtime-services" in runner.call_args.args[0]
    assert result["private_runtime_services"] == {"bus_ready": True}
    assert result["private_window_observation"]["maximum_children"] == 2
    assert "secret" not in json.dumps(result)
    assert result["native_binding_verified"] is False
