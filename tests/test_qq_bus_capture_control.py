"""Mock-only capture/privacy checks, retaining the original bus regression suite."""

import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from tools import qq_bus_admission_control as bus
from tools import qq_bus_capture_control as capture


@pytest.mark.parametrize("text,key", [
    (b"Unknown option --bad", "client_usage_error"),
    (b"Unrecognized option", "client_usage_error"),
    (b"Usage: dbus-send", "client_usage_error"),
    (b"Connection is closed", "connection_closed"),
    (b"Disconnected", "connection_closed"),
    (b"Did not receive a reply", "no_reply_timeout"),
    (b"org.freedesktop.DBus.Error.AccessDenied", "access_denied"),
    (b"Authentication failed", "auth_failure"),
    (b"org.freedesktop.DBus.Error.UnknownMethod", "unknown_method"),
    (b"org.freedesktop.DBus.Error.ServiceUnknown", "service_unknown"),
    (b"Could not get password database information for UID 65534", "unix_identity_lookup_failure"),
    (b"Could not get UID for username", "unix_identity_lookup_failure"),
    (b"Failed to connect", "daemon_unavailable"),
    (b"error while loading shared libraries", "library_load_failure"),
    (b"Machine-id missing", "machine_id_unavailable"),
    (b"Error sending message", "message_generic_error"),
    (b"unclassified", "unknown_failure"),
])
def test_categories(text, key):
    """Recognize canonical public errors without emitting an arbitrary excerpt."""
    result = bus.classify_stderr(text, True)
    assert result["categories"][key]
    assert all(type(value) is bool for value in result["categories"].values())
    assert text.decode() not in json.dumps(result)


def test_caps_and_quoted_options():
    """Only the prefix counts; quoted or embedded arbitrary option wording does not."""
    result = bus.classify_stderr(b"x" * 4096 + b"unknown option", True)
    assert result["prefix_truncated"]
    assert not result["categories"]["client_usage_error"]
    for text in (b'error: "unknown option"', b"error: 'usage'", b"misusage unknown options"):
        assert not bus.classify_stderr(text, True)["categories"]["client_usage_error"]


@pytest.mark.parametrize("outcome", [0, 1, "timeout", "launch"])
def test_private_capture(monkeypatch, outcome):
    """Read at most 4097 private bytes, close capture and never inspect stdout."""
    class Stream(io.BytesIO):
        """Enforce the exact bounded read size."""
        def read(self, size=-1):
            """Reject accidental unbounded capture reads."""
            assert size == 4097
            return super().read(size)
    stream = Stream()
    monkeypatch.setattr(bus.tempfile, "TemporaryFile", lambda: stream)
    def run(command, **kwargs):
        """Keep original argv and one-second options, replacing only stderr."""
        assert command[1] == "--peer=" + bus.ADDRESS
        assert kwargs == dict(stdin=bus.subprocess.DEVNULL, stdout=bus.subprocess.DEVNULL,
                              stderr=stream, timeout=1, check=False, close_fds=True)
        stream.write(b"Connection is closed " + b"SECRET" * 1000)
        if outcome == "timeout":
            raise bus.subprocess.TimeoutExpired(command, 1)
        if outcome == "launch":
            raise OSError("SECRET")
        return SimpleNamespace(returncode=outcome)
    monkeypatch.setattr(bus.subprocess, "run", run)
    result = bus.method_probe("Hello", detailed=True)
    assert stream.closed
    assert result["stderr"]["prefix_truncated"]
    assert result["stderr"]["categories"]["connection_closed"]
    assert "SECRET" not in json.dumps(result)


def test_fixed_callback(monkeypatch):
    """New entry binds detailed=True, with no configurable command or sampler."""
    monkeypatch.setattr(bus, "observe_bus", lambda *args, **kwargs: kwargs)
    assert capture.observe_bus([], {}, None, None, 30, {}) == {"detailed": True}
    monkeypatch.setattr(bus.control, "observe", lambda *args, **kwargs: kwargs)
    assert capture.observe("root") == {"execute": False, "control_mode": "bus_admission_capture_offline"}
    assert bus.control.fixed_sources("bus_admission_capture_offline") == {}
    assert bus.control.base_report("bus_admission_capture_offline")["invocation"]["argv"] == []


def test_child_no_qq(monkeypatch, tmp_path):
    """The capture mode also branches before any source overlay or QQ invocation."""
    monkeypatch.setattr(bus.control.runtime, "validate_root", lambda value: tmp_path)
    monkeypatch.setitem(sys.modules, "qq_bus_capture_control", capture)
    monkeypatch.setattr(bus.control, "fixed_sources", lambda mode: pytest.fail("QQ sources"))
    def child(*args, **kwargs):
        """Assert the fixed capture observer and full existing jail are reused."""
        assert kwargs["application_observer"] is capture.observe_bus
        assert kwargs["filesystem_mode"] == "pivot-root"
        assert kwargs["private_runtime_services"]
        return {"status": "mocked"}
    monkeypatch.setattr(bus.control.runtime, "child_probe", child)
    args = SimpleNamespace(qq_root="root", jail=str(tmp_path), nonce="a" * 32, parent_ns="{}",
                           mount_tool="mount", pivot_tool="pivot", umount_tool="umount",
                           control_mode="bus_admission_capture_offline")
    assert bus.control.child_main(args) == {"status": "mocked"}


def test_frozen_actual_capture_evidence():
    """Pin completed observation, not method success or a wall-clock timeout."""
    artifact = Path(__file__).resolve().parents[1] / "docs" / "qq-bus-capture-evidence.json"
    report = json.loads(artifact.read_text(encoding="utf-8"))
    sample = report["bus_admission"]
    assert report["status"] == "observed" and sample["completed"]
    assert sample["auth"] == "auth_ok"
    assert 0 <= sample["elapsed_seconds"] < 1
    for method in ("hello", "get_id"):
        stage = sample[method]
        assert stage["category"] == "nonzero_exit" and stage["exit_code"] == 1
        assert stage["returned_zero"] is False
        assert stage["stderr"]["present"] and not stage["stderr"]["prefix_truncated"]
        categories = stage["stderr"]["categories"]
        assert {key for key, value in categories.items() if value} == {
            "no_reply_timeout", "message_generic_error"}
        assert all(type(value) is bool for value in categories.values())
    assert sample["identity"]["verified"]
    assert sample["identity"]["uid"] == sample["identity"]["gid"] == [65534] * 4
    assert sample["identity"]["cap_eff_zero"] and sample["identity"]["no_new_privileges"]
    assert not report["application_invoked"] and not report["runtime_executed"]
    assert report["invocation"]["argv"] == []
    assert not report["same_connection_stage_proof"]
    assert report["host_software_unchanged"]
    assert report["host_software_sha256_before"] == report["host_software_sha256_after"]
    assert all(report["offline_network"].values())
    assert not any(report[key] for key in ("safe_to_login", "independent_backend_ready",
                                          "production_cutover", "production_cutover_authorized"))
