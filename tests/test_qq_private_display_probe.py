"""Mock-only checks for the opt-in display; never start Xvfb, QQ or namespaces."""

from contextlib import contextmanager
import json
import stat
import subprocess
import sys
from unittest.mock import MagicMock, Mock

import pytest

from tools import qq_isolated_runtime_probe as probe


@pytest.mark.parametrize("mode,userns,native", [("chroot", False, False),
                                                 ("pivot-root", True, False),
                                                 ("pivot-root", False, True)])
def test_private_display_requires_detached_runtime(mode, userns, native):
    """Do not expand control scope or permit an attached host filesystem root."""
    with pytest.raises(probe.ProbeError, match="private_display_requires_pivot_runtime"):
        probe.validate_private_display_mode(True, mode, userns, native)


def test_private_default_never_starts_display(monkeypatch, tmp_path):
    """Opt-in display without execute validates software but never spawns processes."""
    monkeypatch.setattr(probe.sys, "platform", "linux")
    monkeypatch.setattr(probe, "validate_root", lambda root: tmp_path)
    runner = Mock(side_effect=AssertionError("must not execute"))
    monkeypatch.setattr(probe.subprocess, "run", runner)
    monkeypatch.setattr(probe.subprocess, "Popen", runner)
    report = probe.probe(tmp_path, filesystem_mode="pivot-root", private_display=True)
    assert report["status"] == "not_executed"
    assert report["runtime_executed"] is False
    runner.assert_not_called()


def test_private_parent_forwards_ipc_and_redacts(monkeypatch, tmp_path):
    """Explicit display adds IPC isolation and publishes only fixed boolean fields."""
    monkeypatch.setattr(probe.sys, "platform", "linux")
    monkeypatch.setattr(probe, "validate_root", lambda root: tmp_path)
    monkeypatch.setattr(probe.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(probe.os, "readlink", lambda path: "ipc-parent")
    monkeypatch.setattr(probe, "namespace_ids", lambda: {key: key for key in probe.NAMESPACES})
    monkeypatch.setattr(probe.shutil, "which", lambda name, **kwargs: str(tmp_path / name))
    child = probe.base_report()
    child.update(private_display={"ready": True, "raw": "secret", "failure_category": "secret"},
                 runtime_diagnostics={"dbus_unavailable": True, "secret": True},
                 runtime_record_present=False)
    runner = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(child).encode()))
    monkeypatch.setattr(probe.subprocess, "run", runner)
    report = probe.probe(tmp_path, True, filesystem_mode="pivot-root", private_display=True)
    args = runner.call_args.args[0]
    assert "--ipc" in args and "--private-display" in args
    assert json.loads(args[args.index("--parent-ns") + 1])["ipc"] == "ipc-parent"
    assert report["private_display"] == {"ready": True}
    assert "secret" not in json.dumps(report)
    assert report["runtime_versions_verified"] is False


def test_private_authority_is_cookie_only():
    """Pin the Xauthority wire layout without using any host address or identity."""
    data = probe.private_display_authority(b"x" * 16)
    assert data == b"\xff\xff\x00\x00\x00\x0299\x00\x12MIT-MAGIC-COOKIE-1\x00\x10" + b"x" * 16


def test_display_environment_never_inherits_host(monkeypatch):
    """The base environment never forwards DISPLAY, XAUTHORITY, IPC or session bus."""
    for key in ("DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR"):
        monkeypatch.setenv(key, "host-private")
        assert key not in probe.minimal_environment()


def test_private_cli_has_no_implicit_execution(monkeypatch, capsys):
    """Only the explicitly supplied flags reach probe and execute remains false."""
    runner = Mock(return_value=probe.base_report())
    monkeypatch.setattr(probe, "probe", runner)
    assert probe.main(["--qq-root", "/pristine/QQ", "--filesystem-mode", "pivot-root",
                       "--private-display"]) == 0
    runner.assert_called_once_with("/pristine/QQ", execute=False,
                                  filesystem_mode="pivot-root", private_display=True)
    assert json.loads(capsys.readouterr().out)["runtime_versions_verified"] is False


def test_private_child_keeps_isolation_and_timeout_truth(monkeypatch, tmp_path):
    """Mock kernel changes and verify display starts after detach, never bypassing sandbox."""
    monkeypatch.setitem(sys.modules, "resource", Mock())
    ids = {key: key + "-child" for key in probe.NAMESPACES}
    monkeypatch.setattr(probe, "namespace_ids", lambda: dict(ids))
    monkeypatch.setattr(probe.os, "readlink", lambda path: "ipc-child")
    events = []
    monkeypatch.setattr(probe, "run_checked", lambda args: events.append(args))
    monkeypatch.setattr(probe, "bind_readonly", Mock())
    monkeypatch.setattr(probe.os, "chown", Mock(), raising=False)
    monkeypatch.setattr(probe.shutil, "which", lambda name, **kwargs: str(tmp_path / name))
    monkeypatch.setattr(probe, "enter_filesystem_root", lambda *args: events.append("pivot"))

    @contextmanager
    def display(report):
        """Model a ready private display without creating sockets or subprocesses."""
        assert events[-1] == "pivot"
        report["private_display"] = {"ready": True}
        events.append("display")
        yield dict(probe.minimal_environment(), DISPLAY=":99", XAUTHORITY="/tmp/home/.Xauthority")
        events.append("display_stopped")

    def runtime(command, **kwargs):
        """Write synthetic private failures and model the observed bounded timeout."""
        assert command == ["/opt/QQ/qq", "/probe.js"]
        assert kwargs["preexec_fn"] is probe.drop_runtime_privileges
        assert kwargs["env"]["DISPLAY"] == ":99"
        assert kwargs["timeout"] == 10
        assert kwargs["close_fds"] is True
        kwargs["stderr"].write(b"Failed to connect to the bus: secret /host\nthird_party/crashpad/x")
        raise subprocess.TimeoutExpired(command, 10)

    monkeypatch.setattr(probe, "private_display_session", display)
    monkeypatch.setattr(probe.subprocess, "run", runtime)
    jail = tmp_path / "jail"
    jail.mkdir()
    report = probe.child_probe(tmp_path, jail, "nonce", dict({key: key for key in ids}, ipc="ipc-parent"), "/usr/bin/mount",
                               filesystem_mode="pivot-root", pivot_tool=str(tmp_path / "pivot_root"),
                               umount_tool=str(tmp_path / "umount"), private_display=True)
    assert report["errors"] == ["runtime_timeout"]
    assert report["runtime_executed"] is True
    assert report["runtime_versions_verified"] is False
    assert report["runtime_record_present"] is False
    assert report["runtime_diagnostics"] == {"dbus_unavailable": True,
        "fontconfig_unavailable": False, "shared_memory_unavailable": False,
        "crash_reporter_activity": True}
    assert report["versions"] == {}
    assert "secret" not in json.dumps(report)
    assert events[-1] == "display_stopped"
    shm = [args for args in events if isinstance(args, list) and str(jail / "dev" / "shm") in args]
    assert len(shm) == 1
    assert shm[0][1:4] == ["-t", "tmpfs", "-o"]
    assert "mode=1777" in shm[0][4]


@pytest.mark.parametrize("uid,mode,valid", [(65534, stat.S_IFSOCK, True),
                                            (0, stat.S_IFSOCK, False),
                                            (65534, stat.S_IFREG, False)])
def test_private_session_socket_and_cleanup(monkeypatch, uid, mode, valid):
    """Only the private dropped-UID socket is accepted and every server is terminated."""
    path = MagicMock()
    stream = Mock()
    path.open.return_value.__enter__.return_value = stream
    stream.read.return_value = b"private diagnostic"
    path.lstat.return_value = Mock(st_mode=mode, st_uid=uid)
    monkeypatch.setattr(probe, "Path", lambda *args: path)
    path.__str__.return_value = "/tmp/home/.Xauthority"
    monkeypatch.setattr(probe.os, "chown", Mock(), raising=False)
    process = Mock()
    process.poll.return_value = None
    launcher = Mock(return_value=process)
    monkeypatch.setattr(probe.subprocess, "Popen", launcher)
    report = {}
    if valid:
        with probe.private_display_session(report) as env:
            assert env["DISPLAY"] == ":99"
            assert env["XAUTHORITY"] == "/tmp/home/.Xauthority"
    else:
        with pytest.raises(probe.ProbeError, match="private_display_socket_invalid"):
            with probe.private_display_session(report):
                pytest.fail("invalid socket must not start QQ")
    args = launcher.call_args.args[0]
    assert args == ["/usr/bin/Xvfb", ":99", "-screen", "0", "1024x768x24",
                    "-nolisten", "tcp", "-auth", "/tmp/home/.Xauthority"]
    assert launcher.call_args.kwargs["preexec_fn"] is probe.drop_runtime_privileges
    assert launcher.call_args.kwargs["close_fds"] is True
    process.terminate.assert_called_once()
    assert report["private_display"]["stderr_prefix_bounded"] is True
    assert "private diagnostic" not in json.dumps(report)
