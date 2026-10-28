"""Cross-group archive discovery and targeted recall without policy or prompt heuristics."""

import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def modules():
    """Load only the recorder and retrieval modules, never production clients."""
    loaded = []
    for name in ("history", "cross_scope_memory"):
        path = Path(__file__).resolve().parents[1] / "plugin" / (name + ".py")
        spec = importlib.util.spec_from_file_location("cross_group_recall_" + name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        loaded.append(module)
    return loaded


def persist(root, scope, rows):
    """Create an exact temporary archive with caller-supplied native metadata."""
    (root / (scope + ".jsonl")).write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8")


@pytest.mark.parametrize("cache_block_size", [0, 30])
def test_crowded_out_group_is_discoverable_and_targeted_readable(modules, tmp_path, cache_block_size):
    """A global recent tail must not make an older archive undiscoverable or unreadable."""
    persist(tmp_path, "10", [
        {"ts": index, "line": "busy " + str(index), "record": {"id": index}}
        for index in range(300)])
    native = {"id": "old-message", "sender_id": "participant", "body": "older public evidence"}
    persist(tmp_path, "2", [{"ts": -1, "line": "older public evidence", "record": native}])
    persist(tmp_path, "3", [])
    persist(tmp_path, "private_7", [{"ts": 999, "line": "private secret"}])
    recorder = modules[0].Recorder(str(tmp_path))
    memory = modules[1].CrossScopeMemory(recorder)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    shared = memory.unified_snapshot(cache_block_size=cache_block_size)
    assert {row["scope"] for row in shared["records"]} == {"10"}
    assert 240 <= len(shared["records"]) < 270
    assert shared["coverage"]["archive_scopes"] == ["2", "3", "10"]
    assert shared["coverage"]["returned_scopes"] == ["10"]
    assert shared["coverage"]["scope_count"] == 2
    assert shared["coverage"]["more_available"] is True
    recalled = memory.search("", "10", scope="2")
    assert len(recalled) == 1
    assert recalled[0] == {"scope": "2", "ts": -1, "line": "older public evidence",
                           "record": native, "source": modules[1].SOURCE}
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
    reloaded = modules[1].CrossScopeMemory(modules[0].Recorder(str(tmp_path)))
    assert reloaded.search("", "10", scope="2") == recalled
    assert reloaded.unified_snapshot(cache_block_size=cache_block_size) == shared


def test_scoped_empty_keyword_keeps_native_metadata_and_existing_order(modules, tmp_path):
    """Explicit recent reads preserve timestamps and nested native data without fabricating identities."""
    persist(tmp_path, "2", [
        {"ts": 1, "line": "first"},
        {"ts": 3, "line": "newest", "record": {"quote": {"body": "unaltered"}}},
        {"ts": 2, "line": "middle"},
    ])
    memory = modules[1].CrossScopeMemory(modules[0].Recorder(str(tmp_path)))
    rows = memory.search("   ", "1", scope="2", limit=2)
    assert [row["line"] for row in rows] == ["newest", "middle"]
    assert rows[0]["record"] == {"quote": {"body": "unaltered"}}
    assert "record" not in rows[1]
    assert all(row["source"]["trust"] == "untrusted_data" for row in rows)
    rows[0]["record"]["quote"]["body"] = "changed"
    assert memory.search("", "1", scope="2")[0]["record"]["quote"]["body"] == "unaltered"
    assert memory.search("newest", "1", scope="2")[0]["line"] == "newest"
    assert memory.search("not present", "1", scope="2") == []


def test_empty_keyword_requires_explicit_visible_scope_and_keeps_mute_policy(modules, tmp_path):
    """No query enables private aliases, foreign private scopes, muted reads or global browsing."""
    recorder = modules[0].Recorder(str(tmp_path))
    for scope in ("1", "2", "private:7", "private:8"):
        recorder.append(scope, "evidence " + scope)
    memory = modules[1].CrossScopeMemory(recorder)
    assert memory.search("", "1") == []
    assert memory.search("", "private:7") == []
    assert memory.search("", "1", scope="2", limit=0) == []
    assert memory.search("", "1", scope="2", muted=(value for value in ["2"])) == []
    for current in ("1", "private:7"):
        for target in ("private:8", "private_7", "../2", "default", "0", "１２"):
            assert memory.search("", current, scope=target) == []
    assert [row["scope"] for row in memory.search("", "private:7", scope="private:7")] == ["private:7"]
    assert [row["scope"] for row in memory.search("", "private:7", scope="2")] == ["2"]


def test_coverage_lists_are_archive_only_and_frozen(modules, tmp_path):
    """Discovery lists exclude live caches and private aliases; exclusions affect returned scope only."""
    persist(tmp_path, "2", [{"ts": 1, "line": "current", "record": {"id": 8}}])
    persist(tmp_path, "10", [{"ts": 2, "line": "other"}])
    persist(tmp_path, "private_8", [{"ts": 3, "line": "secret"}])
    memory = modules[1].CrossScopeMemory(modules[0].Recorder(str(tmp_path)),
                                       histories={"3": ["live only"]})
    shared = memory.unified_snapshot(exclude_messages=[("2", 8)], cache_block_size=30)
    assert shared["coverage"]["archive_scopes"] == ["2", "10"]
    assert shared["coverage"]["returned_scopes"] == ["10"]
    shared["coverage"]["archive_scopes"].append("invented")
    shared["coverage"]["returned_scopes"].clear()
    again = memory.unified_snapshot(exclude_messages=[("2", 8)], cache_block_size=30)
    assert again["coverage"]["archive_scopes"] == ["2", "10"]
    assert again["coverage"]["returned_scopes"] == ["10"]
