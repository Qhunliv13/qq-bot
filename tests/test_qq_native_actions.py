"""Offline native recall/upload contracts: no VM, QQ, or production calls."""
import asyncio
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def native():
    """Load the pure helper without importing the plugin runtime."""
    spec = importlib.util.spec_from_file_location("isolated_native_actions", Path(__file__).resolve().parents[1] / "plugin" / "qq_native_actions.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def envelope(data=None, status="ok", retcode=0):
    """Construct a full OneBot receipt, including legitimate null mutation data."""
    return {"status": status, "retcode": retcode, "data": data}


def test_recall_filters_own_messages_and_reverses(native):
    """Only delete the native account's selected messages in legacy reverse order."""
    calls = []
    async def call(action, parameters):
        """Supply identity/history and record exactly one call per mutation."""
        calls.append((action, parameters))
        if action == "get_login_info":
            return envelope({"user_id": 123})
        if action == "get_group_msg_history":
            return envelope({"messages": [{"sender": {"user_id": 123}, "message_id": 1}, {"sender": {"user_id": 999}, "message_id": 2}, {"sender": {"user_id": "123"}, "message_id": 3}]})
        return envelope()
    result = asyncio.run(native.recall("456", 2, False, call))
    assert result["ok"] and result["recall_count"] == 2
    assert calls == [("get_login_info", {}), ("get_group_msg_history", {"group_id": 456, "count": 20}), ("delete_msg", {"message_id": 3}), ("delete_msg", {"message_id": 1})]


@pytest.mark.parametrize("receipt,expected", [(envelope(status="failed", retcode=1), "failed"), ({"status": "ok", "retcode": 0, "data": None, "message": "NapCat4 Is Running"}, "unknown"), ({"status": "ok"}, "unknown"), (envelope(retcode=False), "unknown"), (None, "unknown")])
def test_recall_rejects_false_acknowledgements(native, receipt, expected):
    """HTTP health, malformed, contradictory, and failed responses never prove deletion."""
    calls = []
    async def call(action, parameters):
        """Return valid reads but the specified mutation acknowledgement."""
        calls.append(action)
        if action == "get_login_info":
            return envelope({"user_id": 123})
        if action == "get_group_msg_history":
            return envelope({"messages": [{"sender": {"user_id": 123}, "message_id": 1}]})
        return receipt
    result = asyncio.run(native.recall(456, 1, False, call))
    assert result["ok"] is False and result["recall_count"] == 0
    assert result["results"][0]["status"] == expected
    assert calls.count("delete_msg") == 1


def test_recall_does_not_retry_unknown_mutation(native):
    """A timeout after action submission remains unknown instead of being retried."""
    calls = []
    async def call(action, parameters):
        """Raise after a mutation, without including secret exception text in output."""
        calls.append(action)
        if action == "get_login_info":
            return envelope({"user_id": 123})
        if action == "get_group_msg_history":
            return envelope({"messages": [{"sender": {"user_id": 123}, "message_id": 1}]})
        raise TimeoutError("sensitive transport details")
    result = asyncio.run(native.recall(456, call_action=call))
    assert result["results"][0]["status"] == "unknown"
    assert calls.count("delete_msg") == 1
    assert "sensitive" not in str(result)


@pytest.mark.parametrize("count,all_messages", [(0, False), (-1, False), (True, False), (1, True)])
def test_recall_invalid_arguments_do_not_call_bridge(native, count, all_messages):
    """Never reinterpret invalid count or invent an all-messages destructive mode."""
    async def call(action, parameters):
        """Fail the test if validation permits any bridge access."""
        pytest.fail("unexpected bridge call")
    assert not asyncio.run(native.recall(456, count, all_messages, call))["ok"]


@pytest.mark.parametrize("kind,key,action", [("private", "user_id", "upload_private_file"), ("group", "group_id", "upload_group_file")])
def test_upload_native_route_and_null_ack(native, tmp_path, kind, key, action):
    """Accept null-data native upload acknowledgements without inventing message delivery."""
    file = tmp_path / "example.txt"
    file.write_text("offline test", encoding="utf-8")
    calls = []
    async def call(native_action, parameters):
        """Record native action parameters without performing real uploads."""
        calls.append((native_action, parameters))
        return envelope()
    result = asyncio.run(native.upload(kind, str(file), user_id=123, group_id=456, call_action=call, name="named.txt"))
    assert result["ok"] and result["accepted"]
    assert result["delivered"] is False and "message_id" not in result
    assert result["delivery_confirmation"] == "native_upload_acknowledged"
    assert calls == [(action, {key: 123 if kind == "private" else 456, "file": str(file), "name": "named.txt"})]


def test_upload_health_and_exception_are_not_delivery(native, tmp_path):
    """Root health cannot masquerade as a completed upload, and errors stay sanitized."""
    file = tmp_path / "example.txt"
    file.write_text("offline", encoding="utf-8")
    async def call(action, parameters):
        """Return the observed shape of a root health response."""
        return {**envelope(), "message": "NapCat4 Is Running"}
    result = asyncio.run(native.upload("group", str(file), group_id=456, call_action=call))
    assert not result["ok"] and not result["delivered"]
    async def failed(action, parameters):
        """Emulate uncertain post-submission outcome with a secret-bearing exception."""
        raise TimeoutError("sensitive transport details")
    result = asyncio.run(native.upload("group", str(file), group_id=456, call_action=failed))
    assert result["status"] == "unknown" and "sensitive" not in str(result)


def test_upload_missing_local_file_does_not_call_bridge(native, tmp_path):
    """Reject invalid file paths before issuing a native mutation."""
    async def call(action, parameters):
        """Fail if nonexistent local content reaches the bridge."""
        pytest.fail("unexpected bridge call")
    result = asyncio.run(native.upload("group", str(tmp_path / "missing.txt"), group_id=456, call_action=call))
    assert not result["ok"]


def test_upload_uses_staged_container_namespace(native, tmp_path):
    """Pass the prepared container path, not the inaccessible host path, to OneBot."""
    file = tmp_path / "local.txt"
    file.write_text("offline", encoding="utf-8")
    calls = []
    async def prepare(path):
        """Model a successful staging copy without touching Docker."""
        assert path == str(file)
        return "/tmp/unique-container-file"
    async def call(action, parameters):
        """Verify that the upload sees only the staged path."""
        calls.append(parameters)
        return envelope()
    result = asyncio.run(native.upload("group", str(file), group_id=456, call_action=call, prepare_file=prepare))
    assert result["accepted"] and calls[0]["file"] == "/tmp/unique-container-file"
    assert calls[0]["name"] == "local.txt"


@pytest.mark.parametrize("staged", [None, "relative/path", 123])
def test_upload_invalid_staging_fails_closed(native, tmp_path, staged):
    """Invalid staging never falls back to submitting a host file path."""
    file = tmp_path / "local.txt"
    file.write_text("offline", encoding="utf-8")
    async def prepare(path):
        """Return malformed preparation output without real IO."""
        return staged
    async def call(action, parameters):
        """Any upload after failed staging is a test failure."""
        pytest.fail("native upload must not follow invalid staging")
    result = asyncio.run(native.upload("group", str(file), group_id=456, call_action=call, prepare_file=prepare))
    assert result["error"] == "file_staging_failed" and not result["accepted"]


def test_container_copy_failure_and_unique_destination(native, tmp_path, monkeypatch):
    """Copy failure raises safely; successful copies get non-overwriting UUID paths."""
    file = tmp_path / "local.txt"
    file.write_text("offline", encoding="utf-8")
    calls = []
    def fake_run(arguments, **kwargs):
        """Capture docker arguments, never invoking an external executable."""
        calls.append((arguments, kwargs))
        return type("Completed", (), {"returncode": 0})()
    monkeypatch.setattr(native.subprocess, "run", fake_run)
    first = asyncio.run(native.prepare_container_file(str(file)))
    second = asyncio.run(native.prepare_container_file(str(file)))
    assert first != second and first.startswith("/tmp/higan-upload-")
    assert calls[0][0] == ["docker", "cp", str(file), "napcat:" + first]
    assert calls[0][1]["timeout"] == 120
    def failed_run(arguments, **kwargs):
        """Model docker cp failure without leaking sensitive stderr."""
        return type("Completed", (), {"returncode": 1, "stderr": "sensitive"})()
    monkeypatch.setattr(native.subprocess, "run", failed_run)
    with pytest.raises(RuntimeError, match="^file_staging_failed$"):
        asyncio.run(native.prepare_container_file(str(file)))


@pytest.mark.parametrize("filename,expected", [("a.PNG", "image"), ("a.jpeg", "image"), ("a.MP4", "video"), ("a.mkv", "video"), ("a.SILK", "record"), ("a.wav", "record"), ("a.pdf", "file"), ("no-extension", "file"), ("a.svg", "file")])
def test_media_extension_classification_matches_captured_vm(native, filename, expected):
    """Keep old extension semantics, including case-insensitivity and unknown files."""
    assert native.media_kind(filename) == expected


def test_upload_staging_exception_skips_native_mutation(native, tmp_path):
    """Preparation exceptions remain sanitized and stop before action submission."""
    file = tmp_path / "local.txt"
    file.write_text("offline", encoding="utf-8")
    async def prepare(path):
        """Model an uncertain staging transport without making an upload request."""
        raise TimeoutError("sensitive")
    async def call(action, parameters):
        """No native action may follow preparation failure."""
        pytest.fail("unexpected upload")
    result = asyncio.run(native.upload("private", str(file), user_id=123, call_action=call, prepare_file=prepare))
    assert result["error"] == "file_staging_failed" and "sensitive" not in str(result)
