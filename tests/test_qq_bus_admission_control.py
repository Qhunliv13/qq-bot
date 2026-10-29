"""Mock-only safety and failure coverage; no QQ, socket or namespaces launched."""

import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from tools import qq_bus_admission_control as bus


class FakeSocket:
    """Supply bounded public AUTH responses without a real endpoint."""

    def __init__(self, response=b"", error=None):
        self.response, self.error, self.sent = response, error, None

    def __enter__(self):
        """Use the same context lifetime as a real socket."""
        return self

    def __exit__(self, *args):
        """Close implicitly without inspecting any real file descriptor."""

    def settimeout(self, timeout):
        """Check every operation remains within the one-second stage budget."""
        assert 0 < timeout <= 1

    def connect(self, address):
        """Reject any accidental external or alternative bus address."""
        assert address == "/tmp/runtime/bus"
        if self.error:
            raise self.error

    def sendall(self, data):
        """Record only the fixed numeric EXTERNAL exchange."""
        self.sent = data

    def recv(self, limit):
        """Deliver one byte at a time to exercise framing boundaries."""
        assert limit == 1
        value, self.response = self.response[:1], self.response[1:]
        return value


@pytest.mark.parametrize("response,error,expected", [
    (b"OK " + b"a" * 32 + b"\r\n", None, "auth_ok"),
    (b"", None, "connection_closed"),
    (b"OK short\r\n", None, "auth_not_ok"),
    (b"REJECTED EXTERNAL\r\n", None, "auth_not_ok"),
    (b"x" * 257, None, "response_too_long"),
    (b"", TimeoutError(), "timeout"),
    (b"", OSError(), "socket_error"),
])
def test_auth_categories(monkeypatch, response, error, expected):
    """Preserve stage failures while never returning GUID or daemon response text."""
    connection = FakeSocket(response, error)
    monkeypatch.setattr(bus.socket, "AF_UNIX", 1, raising=False)
    monkeypatch.setattr(bus.socket, "socket", lambda *args: connection)
    assert bus.auth_probe() == expected
    if error is None:
        assert connection.sent == b"\0AUTH EXTERNAL 3635353334\r\n"


@pytest.mark.parametrize("method", ["Hello", "GetId"])
@pytest.mark.parametrize("outcome", [0, 1, "timeout", "launch"])
def test_method_fixed_command(monkeypatch, method, outcome):
    """Each independent public client gets its own bounded, raw-output-free call."""
    def run(command, **kwargs):
        """Validate exact fixed argv and emulate every process outcome."""
        assert command == [bus.CLIENT, ("--peer=" if method == "Hello" else "--bus=") + bus.ADDRESS,
                           "--print-reply", "--reply-timeout=1000", "--dest=org.freedesktop.DBus",
                           "/org/freedesktop/DBus", "org.freedesktop.DBus." + method]
        assert kwargs == dict(stdin=bus.subprocess.DEVNULL, stdout=bus.subprocess.DEVNULL,
                              stderr=bus.subprocess.DEVNULL, timeout=1, check=False, close_fds=True)
        if outcome == "timeout":
            raise bus.subprocess.TimeoutExpired(command, 1)
        if outcome == "launch":
            raise OSError("raw secret error")
        return SimpleNamespace(returncode=outcome)
    monkeypatch.setattr(bus.subprocess, "run", run)
    result = bus.method_probe(method)
    assert result["returned_zero"] is (outcome == 0)
    assert "raw secret" not in json.dumps(result)


def test_callback_reuses_and_restores_sampler(monkeypatch):
    """The no-QQ callback must use the existing privilege fork and restore globals."""
    monkeypatch.setattr(bus.offline, "verify_offline_network", lambda report: None)
    original, limit = bus.application.sample_identity, bus.application.SUMMARY_LIMIT
    def sample(env, timeout):
        """Inspect the exact trusted callback before the existing fork would run."""
        assert bus.application.sample_identity is bus.sample_bus
        assert bus.application.SUMMARY_LIMIT == 16384
        assert timeout == 4
        return {"completed": True}
    monkeypatch.setattr(bus.application, "unprivileged_sample", sample)
    report = {}
    bus.observe_bus(["/opt/QQ/qq"], {}, None, None, 30, report)
    assert bus.application.sample_identity is original
    assert bus.application.SUMMARY_LIMIT == limit
    assert report["application_invoked"] is False
    assert report["runtime_executed"] is False
    assert report["same_connection_stage_proof"] is False


@pytest.mark.parametrize("failure", ["identity", "missing_tool"])
def test_preflight_fail_closed(monkeypatch, failure):
    """Do not contact any socket or execute anything after a failed client gate."""
    monkeypatch.setattr(bus, "client_identity", lambda: {"verified": failure != "identity"})
    monkeypatch.setattr(bus.Path, "is_file", lambda self: False)
    monkeypatch.setattr(bus, "auth_probe", lambda: pytest.fail("unexpected socket"))
    result = bus.sample_bus()
    assert result["completed"] is False
    assert result["auth"] == "not_attempted"


def test_auth_failure_keeps_connections_independent(monkeypatch):
    """A rejected AUTH control does not pretend to gate the separate tool sessions."""
    monkeypatch.setattr(bus, "client_identity", lambda: {"verified": True})
    monkeypatch.setattr(bus.Path, "is_file", lambda self: True)
    monkeypatch.setattr(bus.os, "access", lambda *args: True)
    monkeypatch.setattr(bus, "auth_probe", lambda: "connection_closed")
    monkeypatch.setattr(bus, "method_probe", lambda method: {"category": method})
    result = bus.sample_bus()
    assert result["completed"] is True
    assert result["hello"]["category"] == "Hello"
    assert result["get_id"]["category"] == "GetId"


def test_child_bypasses_qq_sources_and_launch(monkeypatch, tmp_path):
    """The mode branches before overlay, package parsing and all QQ launch code."""
    monkeypatch.setattr(bus.control.runtime, "validate_root", lambda value: tmp_path)
    monkeypatch.setitem(sys.modules, "qq_bus_admission_control", bus)
    monkeypatch.setattr(bus.control, "fixed_sources", lambda mode: pytest.fail("QQ sources accessed"))
    def child(root, jail, nonce, parent, mount, **kwargs):
        """Require the sole isolation implementation and exact trusted callback."""
        assert kwargs["application_observer"] is bus.observe_bus
        assert kwargs["private_display"] and kwargs["private_runtime_services"]
        assert kwargs["filesystem_mode"] == "pivot-root"
        return {"status": "mocked"}
    monkeypatch.setattr(bus.control.runtime, "child_probe", child)
    args = SimpleNamespace(qq_root="root", jail=str(tmp_path), nonce="a" * 32, parent_ns="{}",
                           mount_tool="mount", pivot_tool="pivot", umount_tool="umount",
                           control_mode="bus_admission_offline")
    assert bus.control.child_main(args) == {"status": "mocked"}


def test_fixed_mode_default_and_sources(monkeypatch):
    """No caller command injection and no QQ scripts are required in the new mode."""
    assert bus.control.fixed_sources("bus_admission_offline") == {}
    monkeypatch.setattr(bus.control, "observe", lambda root, **kwargs: kwargs)
    assert bus.observe("root") == {"execute": False, "control_mode": "bus_admission_offline"}
    assert bus.control.base_report("bus_admission_offline")["invocation"]["argv"] == []


def test_actual_baseline_evidence_preserves_limits():
    """Pin measured facts without interpreting nonzero exits as a diagnosed cause."""
    artifact = Path(__file__).resolve().parents[1] / "docs" / "qq-bus-admission-evidence.json"
    report = json.loads(artifact.read_text(encoding="utf-8"))
    sample = report["bus_admission"]
    assert report["status"] == "observed" and sample["completed"] is True
    assert sample["auth"] == "auth_ok"
    for stage in ("hello", "get_id"):
        assert sample[stage] == {"category": "nonzero_exit", "exit_code": 1, "returned_zero": False}
    assert sample["identity"] == {"uid": [65534] * 4, "gid": [65534] * 4,
                                   "cap_eff_zero": True, "no_new_privileges": True,
                                   "verified": True}
    assert 0 <= sample["elapsed_seconds"] < 4
    assert report["same_connection_stage_proof"] is False
    assert sample["same_connection_stage_proof"] is False
    assert report["application_invoked"] is False and report["runtime_executed"] is False
    assert report["invocation"]["argv"] == []
    assert report["host_software_unchanged"] is True
    assert report["host_software_sha256_before"] == report["host_software_sha256_after"]
    assert len(report["host_software_sha256_before"]) == 5
    assert all(report["offline_network"].values())
    for key in ("safe_to_login", "independent_backend_ready", "native_binding_verified",
                "runtime_versions_verified", "production_cutover", "production_cutover_authorized"):
        assert report[key] is False
    # The evidence deliberately contains no error body or attributed failure cause.
    assert "root_cause" not in sample and "raw_stderr" not in sample
