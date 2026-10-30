"""Exercise real fixed-mode dispatch and staging with fakes only; never start QQ."""

import builtins
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock

import pytest

from tools import qq_ephemeral_host_entry as host
from tools import qq_native_connect_control as connect
from tools import qq_native_factory_control as factory
from tools import qq_native_ready_control as ready
from tools import qq_prepared_connect_control as prepared
from tools import qq_private_window_roles as roles
from tools import qq_vendor_bootstrap_control as vendor
from tools import qq_vendor_stall_observation as stall


MODES = ("wrapper", "initialize", "initialize_factories", "connect_offline",
         "connect_ready_offline", "prepared_connect_offline", "vendor_bootstrap",
         "vendor_stall", "vendor_window_roles")
SAMPLED = {"prepared_connect_offline", "vendor_stall", "vendor_window_roles"}
HELPERS = {
    "wrapper": (), "initialize": (),
    "initialize_factories": ("qq_native_connect_control", "qq_native_factory_control"),
    "connect_offline": ("qq_native_connect_control",),
    "connect_ready_offline": ("qq_native_connect_control", "qq_native_ready_control"),
    "prepared_connect_offline": ("qq_native_connect_control", "qq_prepared_connect_control",
                                 "qq_application_observation", "qq_vendor_stall_observation"),
    "vendor_bootstrap": ("qq_native_connect_control", "qq_vendor_bootstrap_control"),
    "vendor_stall": ("qq_native_connect_control", "qq_vendor_bootstrap_control",
                     "qq_application_observation", "qq_vendor_stall_observation"),
    "vendor_window_roles": ("qq_native_connect_control", "qq_vendor_bootstrap_control",
                            "qq_application_observation", "qq_vendor_stall_observation",
                            "qq_private_window_roles"),
}


@pytest.fixture
def imports(monkeypatch):
    """Mirror copied flat imports without loading native libraries or private X."""
    for module in (host, connect, factory, ready, prepared, vendor, stall, roles, stall.application):
        monkeypatch.setitem(sys.modules, module.__name__.split(".")[-1], module)


@pytest.fixture
def observer(monkeypatch, imports):
    """Disable only OS/native boundaries while retaining the real observer dispatch."""
    monkeypatch.setattr(host, "stage_control", mock.Mock())
    monkeypatch.setattr(host, "start_read_watches", lambda: (1, {}))
    monkeypatch.setattr(host, "finish_read_watches", lambda *_args: {})
    monkeypatch.setattr(connect, "verify_offline_network", mock.Mock())
    # Missing final/progress files must remain incomplete, not inferred success.
    monkeypatch.setattr(Path, "open", mock.Mock(side_effect=FileNotFoundError))
    process = mock.Mock(pid=23, args=["/opt/QQ/qq"], returncode=-9)
    process.poll.return_value = None
    monkeypatch.setattr(host.subprocess, "Popen", mock.Mock(return_value=process))
    return process


def invoke(mode, output, errors):
    """Call the actual fixed observer with unchanged executable and 30-second bound."""
    report = host.base_report(mode)
    # The shared child runtime supplies isolation before calling this observer.
    report["isolation"] = {}
    host.observe_host(["/opt/QQ/qq"], {}, output, errors, 30, report,
                      source=b"fixed", control=b"fixed", mount="mount", parent_ids={},
                      nonce="a" * 32, control_mode=mode)
    return report


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("timed_out", [False, True])
def test_actual_wait_dispatch_and_capture(monkeypatch, observer, mode, timed_out):
    """Only prepared joins existing sampled modes; exit/timeout cleanup stays shared."""
    waiter = mock.Mock()
    capture = mock.Mock()
    monkeypatch.setattr(stall, "observe_wait", waiter)
    monkeypatch.setattr(stall, "capture_categories", capture)
    if timed_out:
        # Only the primary wait times out; the post-kill reap remains bounded at 2s.
        if mode in SAMPLED:
            waiter.side_effect = subprocess.TimeoutExpired(observer.args, 30)
        else:
            observer.wait.side_effect = [subprocess.TimeoutExpired(observer.args, 30), -9]
    report = invoke(mode, io.BytesIO(), io.BytesIO())
    if mode in SAMPLED:
        expected = {"timeout": 30, "report": report}
        if mode == "vendor_window_roles":
            expected["window_sampler"] = roles.sample_roles
        waiter.assert_called_once_with(observer, {"QQ_WRAPPER_HOST_NONCE": "a" * 32}, **expected)
        capture.assert_called_once()
        observer.wait.assert_called_once_with(timeout=2)
    else:
        waiter.assert_not_called()
        capture.assert_not_called()
        assert observer.wait.call_args_list == [mock.call(timeout=30), mock.call(timeout=2)]
    observer.kill.assert_called_once()
    assert report["completion"] == ("watchdog_30s" if timed_out else "application_exited")
    assert report["host_record"] is None
    assert report["status"] == "failed"
    for key in ("native_login_verified", "session_initialized", "full_sendrecv_verified"):
        assert report[key] is False
    if mode not in ("wrapper", "initialize"):
        connect.verify_offline_network.assert_called_once_with(report)


def test_prepared_real_waiter_with_mocked_privileged_sampler(monkeypatch, observer):
    """Real dispatch reaches shared sampler for 30s, preserving identity and cleanup."""
    clock = [0.0]
    original = stall.application.sample_identity

    def sleep(seconds):
        """Advance fake monotonic time without a wall-clock delay."""
        clock[0] += max(seconds, 0.001)

    def sample(env, timeout):
        """Assert shared bounded-fork entry is used; return only synthetic safe facts."""
        assert stall.application.sample_identity is stall.trusted_sample
        assert stall.MAIN_PID == observer.pid
        assert stall.WINDOW_SAMPLER is None
        assert 0 < timeout <= 0.75
        assert env == {"QQ_WRAPPER_HOST_NONCE": "a" * 32}
        return {"identity_verified": True,
                "main": {"state": "S", "utime_ticks": 1, "stime_ticks": 0, "start_ticks": 42},
                "windows": {"roles_queried": False}}

    monkeypatch.setattr(stall.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(stall.time, "sleep", sleep)
    monkeypatch.setattr(stall.os, "sysconf", lambda _name: 100, raising=False)
    sampler = mock.Mock(side_effect=sample)
    monkeypatch.setattr(stall.application, "unprivileged_sample", sampler)
    # Real file descriptors exercise shared fstat/category capture, not a copied parser.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        errors.write(b"File too large; private detail never retained")
        report = invoke("prepared_connect_offline", output, errors)
    assert sampler.call_count == 30
    assert 30 <= clock[0] < 30.1
    assert report["completion"] == "watchdog_30s"
    assert report["stall_observation"]["window_roles_queried"] is False
    assert report["stall_observation"]["summary"]["classification"] == "predominantly_waiting_observed"
    assert report["stall_observation"]["summary"]["root_cause_proven"] is False
    assert report["stall_capture"]["stderr_categories"]["file_size_limit"] is True
    assert report["stall_capture"]["resource_bounds_unchanged"] == {
        "file_size_bytes": 16384, "open_files": 128, "cpu_seconds": 30}
    assert "private detail" not in json.dumps(report)
    assert stall.application.sample_identity is original
    assert (stall.MAIN_PID, stall.MAIN_START, stall.WINDOW_SAMPLER) == (None, None, None)
    assert report["bounded_progress_verified"] is False


@pytest.mark.parametrize("mode", MODES)
def test_helpers_preimported_before_pivot(monkeypatch, tmp_path, imports, mode):
    """Execute child setup: all required helpers load before the shared pivot callback."""
    root = tmp_path / "QQ"
    app = root / "resources/app"
    (app / "app_launcher").mkdir(parents=True)
    (app / "package.json").write_bytes(b"manifest")
    (app / "app_launcher/index.js").write_bytes(b"vendor")
    monkeypatch.setattr(host.runtime, "validate_root", lambda _path: root)
    monkeypatch.setattr(host, "validate_manifest", lambda _raw: b"control")
    monkeypatch.setattr(vendor, "validate_vendor_entry", mock.Mock())
    seen = []
    original = builtins.__import__

    def track(name, *args, **kwargs):
        """Record flat preimports in execution order without changing module resolution."""
        seen.append(name)
        return original(name, *args, **kwargs)

    def pivot(*args, **kwargs):
        """Represent the detach boundary; every fixed helper must already be cached."""
        assert set(HELPERS[mode]).issubset(seen)
        assert kwargs["filesystem_mode"] == "pivot-root"
        assert kwargs["private_display"] and kwargs["private_runtime_services"]
        assert kwargs["extended_startup"]
        return {"status": "test"}

    monkeypatch.setattr(builtins, "__import__", track)
    monkeypatch.setattr(host.runtime, "child_probe", pivot)
    args = SimpleNamespace(qq_root=str(root), control_mode=mode, parent_ns="{}",
                           jail=str(tmp_path / "jail"), nonce="a" * 32,
                           mount_tool="mount", pivot_tool="pivot_root", umount_tool="umount")
    assert host.child_main(args) == {"status": "test"}
    assert {name for name in seen if name in set().union(*HELPERS.values())} == set(HELPERS[mode])


@pytest.mark.parametrize("mode", MODES)
def test_outer_copy_set_matches_preimports(monkeypatch, tmp_path, mode):
    """Execute outer staging with a fake launcher; no missing or expanded dependencies."""
    root = tmp_path / "QQ"
    app = root / "resources/app"
    app.mkdir(parents=True)
    (app / "package.json").write_bytes(b"manifest")
    monkeypatch.setattr(host.sys, "platform", "linux")
    monkeypatch.setattr(host.runtime, "validate_root", lambda _path: root)
    monkeypatch.setattr(host, "validate_manifest", lambda _raw: b"control")
    monkeypatch.setattr(host, "digest", lambda _path: "unchanged")
    monkeypatch.setattr(host.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(host.os, "readlink", lambda _path: "parent-ipc")
    monkeypatch.setattr(host.runtime, "namespace_ids", lambda: {"pid": "parent-pid"})
    monkeypatch.setattr(host.shutil, "which", lambda name, **_kwargs: "/usr/bin/" + name)
    copy = mock.Mock()
    monkeypatch.setattr(host.shutil, "copyfile", copy)
    commands = []

    def launch(command, **kwargs):
        """Return sanitized fake child output without unshare, QQ, or real subprocesses."""
        commands.append(command)
        kwargs["stdout"].write(json.dumps(host.base_report(mode)).encode())
        process = mock.Mock(returncode=0)
        process.poll.return_value = 0
        return process

    monkeypatch.setattr(host.subprocess, "Popen", launch)
    report = host.observe(root, execute=True, control_mode=mode)
    assert report["status"] == "not_executed"
    copied = {call.args[0].name for call in copy.call_args_list}
    expected = {"qq_ephemeral_host_entry.py", "qq_isolated_runtime_probe.py"}
    expected.update(path.name for path in host.fixed_sources(mode).values())
    expected.update(name + ".py" for name in HELPERS[mode])
    assert copied == expected
    assert len(copy.call_args_list) == len(expected)
    assert commands[0][-2:] == ["--control-mode", mode]
    assert report["host_software_unchanged"] is True


def test_fixed_prepared_sources_and_timeouts_unchanged():
    """No additional mode or JS source is needed for external observation."""
    sources = host.fixed_sources("prepared_connect_offline")
    assert {path.name for path in sources.values()} == {
        "qq_prepared_connect_entry.js", "native_host.js", "native_login.js", "native_metadata.js"}
    assert (host.QQ_TIMEOUT, host.OUTER_TIMEOUT, host.LIMIT) == (30, 45, 16384)
    with pytest.raises(host.runtime.ProbeError):
        host.fixed_sources("prepared_stall")


def test_frozen_prepared_stall_evidence():
    """Validate recorded facts without rerunning QQ or pinning future working sources."""
    docs = Path(__file__).resolve().parents[1] / "docs" / "evidence"
    report = json.loads((docs / "qq-prepared-stall-evidence.json").read_text(encoding="utf-8"))
    previous = json.loads((docs / "qq-prepared-connect-evidence.json").read_text(encoding="utf-8"))
    progress = report["connect_progress"]
    raw = (json.dumps(progress) + "\n").encode()
    assert prepared.parse_progress(raw, progress["nonce"]) == progress
    assert progress["stage"] == "after_connect"
    assert not any(progress["login"]["native_callback_counts"].values())
    assert report["completion"] == "watchdog_30s"
    assert report["exit_code"] == -9 and report["host_record"] is None
    assert report["bounded_progress_verified"] is False
    assert report["host_software_sha256_before"] == report["host_software_sha256_after"]
    assert report["host_software_unchanged"] is True
    assert all(report["offline_network"].values())
    observation = report["stall_observation"]
    rows = observation["samples"]
    assert len(rows) == observation["maximum_samples"] == 30
    assert observation["maximum_threads_per_sample"] == 64
    assert observation["window_roles_queried"] is False
    assert all(row["identity_verified"] for row in rows)
    times = [row["elapsed_seconds"] for row in rows]
    assert 0 <= times[0] < times[-1] <= 30
    assert all(left < right for left, right in zip(times, times[1:]))
    assert len({row["main"]["start_ticks"] for row in rows}) == 1
    expected_limits = {"cpu_seconds": {"soft": 30, "hard": 30},
                       "file_size_bytes": {"soft": 16384, "hard": 16384},
                       "open_files": {"soft": 128, "hard": 128}}
    for row in rows:
        assert row["main"]["limits"] == expected_limits
        assert len(row["main"]["threads"]) <= 64
        assert not row["main"]["threads_truncated"]
        assert row["windows"]["roles_queried"] is False
    summary = observation["summary"]
    assert summary == stall.summarize(rows, summary["ticks_per_second"])
    assert summary["classification"] == "predominantly_waiting_observed"
    assert summary["cpu_tick_delta"] == 378 and summary["root_cause_proven"] is False
    # The coarse unavailable/unknown category cannot identify a kernel/native wait.
    assert {thread["wait_category"] for row in rows for thread in row["main"]["threads"]} == {"other"}
    assert report["authored_source_sha256"] == previous["authored_source_sha256"]
    changed = {key for key, value in report["staged_source_sha256"].items()
               if previous["staged_source_sha256"].get(key) != value}
    # Compare immutable observations only: the login fix forbids single-variable claims.
    assert changed == {"/tmp/native_login.js"}
