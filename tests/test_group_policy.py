"""Isolated policy-store regressions: no plugin runtime, network, or production IO."""

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import stat
import threading

import pytest


@pytest.fixture
def policy():
    """Load only the generic store to avoid production plugin imports."""
    path = Path(__file__).resolve().parents[1] / "plugin" / "group_policy.py"
    spec = importlib.util.spec_from_file_location("isolated_group_policy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def store(policy, tmp_path):
    """Use a nonexistent isolated file; defaults must not create it."""
    return policy.GroupPolicyStore(tmp_path / "groups.json")


def seed(store, data):
    """Prepare legacy on-disk state without involving production configuration."""
    store.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_missing_and_show_do_not_create_file(store):
    """Missing policy means defaults, and show is strictly read-only."""
    expected = {"mention_only": [], "muted": [], "poll_minutes": {}, "reply_pct": {}}
    assert store.read() == expected
    assert store.update(None, "show") == {**expected, "ok": True, "group": "", "mode": "show"}
    assert not store.path.exists()


def test_read_normalizes_without_mutating_file_or_losing_extras(store):
    """Known fields normalize individually while arbitrary JSON extras survive."""
    data = {
        "mention_only": [42, "42", "43", "00044"], "muted": [43],
        "poll_minutes": {"42": "3", "43": 0, "44": 9999},
        "reply_pct": {"42": "12.5", "43": -10, "44": 200},
        "extra": {"label": "测试", "items": [1]},
    }
    seed(store, data)
    before = store.path.read_bytes()
    normalized = store.read()
    assert normalized["mention_only"] == ["42", "44"]
    assert normalized["muted"] == ["43"]
    assert normalized["poll_minutes"] == {"42": 3, "43": 1, "44": 1440}
    assert normalized["reply_pct"] == {"42": 12.5, "43": 0.0, "44": 100.0}
    normalized["extra"]["items"].append(2)
    assert store.read()["extra"] == data["extra"]
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("mode", ["normal", "mention_only", "muted"])
def test_restrictive_and_normal_modes_reset_only_target_overrides(store, mode):
    """Explicit base modes are exclusive and reset only that group's overrides."""
    seed(store, {
        "mention_only": [42, 43], "muted": [42, 44],
        "poll_minutes": {"42": 5, "43": 9}, "reply_pct": {"42": 25, "43": 50},
        "extra": {"untouched": True},
    })
    result = store.update("42", mode)
    assert result["ok"] is True
    assert ("42" in result["mention_only"]) == (mode == "mention_only")
    assert ("42" in result["muted"]) == (mode == "muted")
    assert result["poll_minutes"] == {"43": 9}
    assert result["reply_pct"] == {"43": 50}
    assert result["extra"] == {"untouched": True}
    before = store.path.read_bytes()
    store.update(42, mode)
    assert store.path.read_bytes() == before
    assert store.read()["mention_only"] == result["mention_only"]


def test_poll_is_persisted_clears_restrictions_preserves_probability(store):
    """Polling enables the group without silently deleting its reply probability."""
    seed(store, {"mention_only": [42], "muted": [43], "reply_pct": {"42": 27, "43": 0}})
    result = store.update(42, "poll", minutes=7)
    assert result["minutes"] == 7
    assert result["mention_only"] == []
    assert result["muted"] == ["43"]
    assert result["reply_pct"] == {"42": 27, "43": 0}
    assert json.loads(store.path.read_text(encoding="utf-8"))["poll_minutes"] == {"42": 7}
    assert store.update(43, "poll")["muted"] == []
    assert store.read()["poll_minutes"] == {"42": 7, "43": 10}


def test_probability_preserves_all_existing_modes(store):
    """Probability changes only its override, even while muted or mention-only."""
    seed(store, {"mention_only": [42], "muted": [43], "poll_minutes": {"42": 8}})
    result = store.update(42, "probability", pct=25)
    assert result["pct"] == 25
    assert result["mention_only"] == ["42"]
    assert result["muted"] == ["43"]
    assert result["poll_minutes"] == {"42": 8}
    assert store.update(43, "probability", minutes=40)["pct"] == 40
    assert store.update(44, "probability")["pct"] == 100
    assert store.read()["muted"] == ["43"]


@pytest.mark.parametrize("mode,kwargs,key,expected", [
    ("probability", {"pct": -3}, "pct", 0),
    ("probability", {"pct": 101}, "pct", 100),
    ("probability", {"pct": 0}, "pct", 0),
    ("poll", {"minutes": 0}, "minutes", 1),
    ("poll", {"minutes": 9999}, "minutes", 1440),
    ("poll", {"minutes": "2.9"}, "minutes", 2),
])
def test_numeric_values_follow_legacy_bounds(store, mode, kwargs, key, expected):
    """Valid finite overrides retain established clipping/truncation behavior."""
    assert store.update(42, mode, **kwargs)[key] == expected
    assert store.read()


@pytest.mark.parametrize("group", [None, "", "private", "private:42", 0, -1, True, 1.2, "1e2", "-1", "１２", [], "00"])
def test_invalid_groups_cannot_write(store, group):
    """Private scopes, zero, booleans, and ambiguous identifiers are rejected."""
    with pytest.raises(ValueError):
        store.update(group, "normal")
    assert not store.path.exists()


@pytest.mark.parametrize("mode", ["25", "25%", "NORMAL", " normal ", "unknown", None, []])
def test_only_exact_supported_modes_are_accepted(store, mode):
    """A pct argument must not let an unsupported mode reach a write path."""
    with pytest.raises(ValueError):
        store.update(42, mode, pct=25)
    assert not store.path.exists()


def test_extra_pct_does_not_override_explicit_mode_or_show(store):
    """Supported explicit modes are authoritative rather than pct aliases."""
    assert store.update(42, "normal", pct=25)["mode"] == "normal"
    before = store.path.read_bytes()
    assert store.update(42, "show", pct=25)["mode"] == "show"
    assert store.path.read_bytes() == before
    assert store.read()["reply_pct"] == {}


@pytest.mark.parametrize("value", ["bad", float("nan"), float("inf"), True, [], {}])
@pytest.mark.parametrize("mode,key", [("probability", "pct"), ("poll", "minutes")])
def test_invalid_overrides_fail_without_writing(store, value, mode, key):
    """NaN, infinity, and malformed values cannot corrupt the policy file."""
    store.update(42, "muted")
    before = store.path.read_bytes()
    with pytest.raises(ValueError):
        store.update(42, mode, **{key: value})
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("contents", [
    "", "{broken", "[]", "null", '{"mention_only": "42"}',
    '{"muted": null}', '{"poll_minutes": []}', '{"reply_pct": {"42": "bad"}}',
    '{"poll_minutes": {"42": NaN}}', '{"mention_only": ["private"]}',
    '{"extra": NaN}', '{"extra": Infinity}',
])
def test_invalid_file_never_falls_back_to_empty_or_gets_overwritten(store, contents):
    """Corrupt files raise on both reads and updates, preserving forensic data."""
    store.path.write_text(contents, encoding="utf-8")
    with pytest.raises(ValueError):
        store.read()
    with pytest.raises(ValueError):
        store.update(42, "normal")
    assert store.path.read_text(encoding="utf-8") == contents


def test_invalid_utf8_is_not_empty_config(store):
    """Invalid encodings fail closed and must never be replaced by defaults."""
    contents = b'{"extra": "\xff"}'
    store.path.write_bytes(contents)
    with pytest.raises(UnicodeError):
        store.read()
    with pytest.raises(UnicodeError):
        store.update(42, "normal")
    assert store.path.read_bytes() == contents


def test_io_read_errors_are_not_empty_config(store, monkeypatch):
    """Permission errors are operational failures, not absent policy files."""
    def denied(*args, **kwargs):
        """Simulate a persistent unreadable policy without changing host ACLs."""
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "open", denied)
    with pytest.raises(PermissionError):
        store.read()
    with pytest.raises(PermissionError):
        store.update(42, "normal")


@pytest.mark.parametrize("failure", ["replace", "fsync", "chmod", "dump"])
def test_write_failures_propagate_and_preserve_old_file(store, policy, monkeypatch, failure):
    """Precommit failures never return success or leave partial/temporary files."""
    store.update(42, "muted")
    before = store.path.read_bytes()

    def fail(*args, **kwargs):
        """Inject one filesystem/serialization failure before atomic replace."""
        raise OSError("injected failure")

    target = policy.json if failure == "dump" else policy.os
    monkeypatch.setattr(target, failure, fail)
    with pytest.raises(OSError, match="injected failure"):
        store.update(42, "normal")
    assert store.path.read_bytes() == before
    assert list(store.path.parent.glob(".groups.json.*.tmp")) == []


def test_atomic_replace_fsync_order_and_private_mode(store, policy, monkeypatch):
    """Replacement sees a complete flushed private file in the same directory."""
    events = []
    real_fsync = policy.os.fsync
    real_replace = policy.os.replace

    def fsync(fd):
        """Record durability barriers while exercising the actual filesystem."""
        events.append("fsync")
        return real_fsync(fd)

    def replace(source, destination):
        """Inspect the complete temporary snapshot at the atomic commit point."""
        source = Path(source)
        assert source.parent == store.path.parent
        assert json.loads(source.read_text(encoding="utf-8"))["muted"] == ["42"]
        if os.name != "nt":
            assert stat.S_IMODE(source.stat().st_mode) == 0o600
        assert events == ["fsync"]
        events.append("replace")
        return real_replace(source, destination)

    monkeypatch.setattr(policy.os, "fsync", fsync)
    monkeypatch.setattr(policy.os, "replace", replace)
    store.update(42, "muted")
    assert events == (["fsync", "replace"] if os.name == "nt" else ["fsync", "replace", "fsync"])
    if os.name != "nt":
        assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert store.read()["muted"] == ["42"]


@pytest.mark.skipif(os.name == "nt", reason="Windows has no portable directory fsync")
def test_directory_sync_failure_cannot_report_success(store, policy, monkeypatch):
    """Postcommit durability failure is still raised instead of claiming success."""
    real_fsync = policy.os.fsync
    calls = []

    def fail_directory(fd):
        """Fail only the second barrier, after the atomic rename has committed."""
        calls.append(fd)
        if len(calls) == 2:
            raise OSError("directory sync failed")
        return real_fsync(fd)

    monkeypatch.setattr(policy.os, "fsync", fail_directory)
    with pytest.raises(OSError, match="directory sync failed"):
        store.update(42, "muted")
    assert store.read()["muted"] == ["42"]


def test_canonical_aliases_share_rlock_and_concurrent_updates(policy, tmp_path):
    """Instances lock the entire read-modify-write transaction, avoiding lost updates."""
    first = policy.GroupPolicyStore(tmp_path / "groups.json")
    alias = policy.GroupPolicyStore(tmp_path / "unused" / ".." / "groups.json")
    assert first.path == alias.path
    assert first._lock is alias._lock
    barrier = threading.Barrier(8)

    def update_batch(worker):
        """Start competing stores together and retain every independent group's update."""
        barrier.wait(timeout=10)
        for offset in range(10):
            instance = first if worker % 2 else alias
            instance.update(worker * 10 + offset + 1, "probability", pct=worker)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(update_batch, range(8)))
    expected = {str(worker * 10 + offset + 1): worker for worker in range(8) for offset in range(10)}
    assert first.read()["reply_pct"] == expected
    assert alias.read() == first.read()


def test_result_reserved_fields_cannot_be_spoofed_by_extras(store):
    """Unknown file metadata survives storage but cannot falsify operation status."""
    seed(store, {"ok": False, "mode": "spoofed", "group": "private"})
    assert store.update(42, "show")["ok"] is True
    result = store.update(42, "normal")
    assert (result["ok"], result["mode"], result["group"]) == (True, "normal", "42")
    assert store.read()["ok"] is False
