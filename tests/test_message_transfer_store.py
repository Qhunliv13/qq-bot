"""Private clipboard contracts using temporary databases, never a live QQ runtime."""

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
from pathlib import Path
import sqlite3
import uuid

import pytest


@pytest.fixture
def store_module():
    """Load only the storage module so tests cannot start the QQ plugin runtime."""
    path = Path(__file__).resolve().parents[1] / "plugin" / "message_transfer_store.py"
    spec = importlib.util.spec_from_file_location("isolated_message_transfer_store", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def message():
    """Represent native segments and unmodified sender/time/source metadata."""
    return {"source_group_id": "123", "source_message_id": 456, "message": [{"type": "text", "data": {"text": "  原文\n"}}, {"type": "image", "data": {"url": "https://example.invalid/private?token=secret"}}], "sender": {"user_id": 789, "nickname": "Sender"}, "time": 123456789, "metadata": {"native": True, "nullable": None, "ratio": 0.5}}


def test_snapshot_persistence_and_receipt_privacy(store_module, tmp_path, message):
    """Reopening preserves original native JSON while public receipts omit content."""
    path = tmp_path / "private" / "transfers.sqlite3"
    store = store_module.TransferStore(path)
    receipt = store.put("private:caller-1", message)
    assert set(receipt) == {"clipboard_id", "clip_id", "source_group_id", "source_message_id"}
    assert receipt["clipboard_id"] == receipt["clip_id"]
    assert uuid.UUID(hex=receipt["clipboard_id"]).version == 4
    assert receipt["source_group_id"] == "123"
    assert type(receipt["source_message_id"]) is int
    assert "secret" not in str(receipt) and "原文" not in str(receipt)
    assert store_module.TransferStore(path).get("private:caller-1", receipt["clipboard_id"]) == message


def test_exact_scope_ownership_and_no_global_fallback(store_module, tmp_path, message):
    """Even possession of another clipboard's opaque ID does not cross ownership."""
    store = store_module.TransferStore(tmp_path / "clips.sqlite3")
    first = store.put("private:alice", message)
    assert store.get("private:bob", first["clipboard_id"]) is None
    assert store.get("group:123", first["clipboard_id"]) is None
    assert store.get("private:alice ", first["clipboard_id"]) is None
    assert store.get("private:alice", "unknown") is None
    second = store.put("private:bob", message)
    assert second["clipboard_id"] != first["clipboard_id"]
    assert store.get("private:alice", second["clipboard_id"]) is None


def test_put_is_idempotent_across_reopen_and_key_order(store_module, tmp_path, message):
    """Canonical full-payload keys deduplicate retries without deterministic public IDs."""
    path = tmp_path / "clips.sqlite3"
    store = store_module.TransferStore(path)
    first = store.put("caller", message)
    reordered = dict(reversed(list(message.items())))
    assert store_module.TransferStore(path).put("caller", reordered) == first
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM message_clipboards").fetchone()[0] == 1


def test_input_and_retrieved_mutations_are_detached(store_module, tmp_path, message):
    """Neither caller mutations nor returned mutable values can modify a saved clip."""
    store = store_module.TransferStore(tmp_path / "clips.sqlite3")
    receipt = store.put("caller", message)
    message["message"][0]["data"]["text"] = "changed input"
    snapshot = store.get("caller", receipt["clipboard_id"])
    assert snapshot["message"][0]["data"]["text"] == "  原文\n"
    snapshot["sender"]["nickname"] = "changed output"
    assert store.get("caller", receipt["clipboard_id"])["sender"]["nickname"] == "Sender"
    changed = store.put("caller", message)
    assert changed["clipboard_id"] != receipt["clipboard_id"]
    assert store.get("caller", receipt["clipboard_id"])["message"][0]["data"]["text"] == "  原文\n"


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf"), {1: "lossy key"}, ("tuple",), {"set"}, b"bytes", object()])
def test_non_json_and_nonfinite_rejected_before_database_creation(store_module, tmp_path, message, invalid):
    """Invalid native metadata cannot silently coerce or cause an on-disk write."""
    path = tmp_path / "not-created.sqlite3"
    message["invalid"] = invalid
    with pytest.raises(ValueError):
        store_module.TransferStore(path).put("caller", message)
    assert not path.exists()


@pytest.mark.parametrize("changes", [{"source_group_id": True}, {"source_message_id": False}, {"source_group_id": None}, {"source_message_id": ""}, {"message": "text"}, {"message": None}])
def test_required_native_schema_rejected(store_module, tmp_path, message, changes):
    """Require explicit source identifiers and an actual segment list, not invented data."""
    message.update(changes)
    with pytest.raises(ValueError):
        store_module.TransferStore(tmp_path / "clips.sqlite3").put("caller", message)


@pytest.mark.parametrize("scope", [None, "", "  ", 123, True])
def test_explicit_scope_required(store_module, tmp_path, message, scope):
    """An absent scope must never fall back to a globally shared clipboard."""
    store = store_module.TransferStore(tmp_path / "clips.sqlite3")
    with pytest.raises(ValueError):
        store.put(scope, message)
    with pytest.raises(ValueError):
        store.get(scope, "anything")


def test_concurrent_identical_puts_share_one_receipt(store_module, tmp_path, message):
    """SQLite uniqueness makes concurrent local retries converge on the first snapshot."""
    store = store_module.TransferStore(tmp_path / "clips.sqlite3")
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(lambda unused: store.put("caller", message), range(16)))
    assert all(receipt == receipts[0] for receipt in receipts)


def test_empty_database_lookup_and_invalid_id(store_module, tmp_path):
    """Missing clips read as None, whereas malformed lookup arguments fail explicitly."""
    store = store_module.TransferStore(tmp_path / "private" / "clips.sqlite3")
    assert store.get("caller", "unknown") is None
    for invalid in (None, "", True):
        with pytest.raises(ValueError):
            store.get("caller", invalid)


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permissions do not exist on Windows")
def test_private_database_permissions(store_module, tmp_path, message):
    """Database payloads, including secret media URLs, are owner-readable only."""
    path = tmp_path / "private" / "clips.sqlite3"
    store = store_module.TransferStore(path)
    store.put("caller", message)
    assert path.stat().st_mode & 0o777 == 0o600
    path.chmod(0o644)
    store.get("caller", "unknown")
    assert path.stat().st_mode & 0o777 == 0o600
