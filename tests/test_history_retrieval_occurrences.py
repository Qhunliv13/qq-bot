"""Occurrence-safe history retrieval without guessing forwards from copied text."""

import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def memory(tmp_path):
    """Load isolated retrieval modules against disposable native JSONL records."""
    modules = []
    for name in ("history", "cross_scope_memory"):
        path = Path(__file__).resolve().parents[1] / "plugin" / (name + ".py")
        spec = importlib.util.spec_from_file_location("occurrences_" + name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    return modules[1].CrossScopeMemory(modules[0].Recorder(str(tmp_path)))


def persist(root, scope, rows):
    """Persist caller-defined metadata unchanged so provenance assertions are exact."""
    (root / (scope + ".jsonl")).write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def test_repeated_scoped_native_id_is_not_another_occurrence(memory, tmp_path):
    """Repeated archival delivery is deduplicated, not distinct IDs or copied bodies."""
    original = {"ts": 1, "line": "same forward", "record": {
        "id": "native-1", "sender": {"id": "sender"}, "body": "same forward",
        "segments": [{"type": "forward", "data": {"id": "payload"}}]}}
    persist(tmp_path, "2", [original,
        {"ts": 99, "line": "duplicate override", "record": {"id": "native-1"}},
        {"ts": 2, "line": "same forward", "record": {"id": "native-2"}},
        {"ts": 3, "line": "same forward"}, {"ts": 4, "line": "same forward"},
        {"ts": 5, "line": "same forward", "record": {"id": ""}},
        {"ts": 6, "line": "same forward", "record": {"id": ""}}])
    persist(tmp_path, "3", [{"ts": 7, "line": "same forward", "record": {"id": "native-1"}}])
    rows = memory.search("same forward", "1", limit=20)
    assert len(rows) == 7
    assert rows[-1]["record"] == original["record"]
    assert rows[-1]["ts"] == 1
    assert memory.search("duplicate override", "1") == []
    assert memory.search("native-1", "1", scope="2")[0]["record"] == original["record"]
    assert memory.search("", "1", scope="2", limit=20)[-1]["record"] == original["record"]


def test_quote_reference_stays_its_own_native_parent(memory, tmp_path):
    """A quote hit retains the quoting sender and ID, not the quoted occurrence ID."""
    quote = {"id": "quote-parent", "sender": {"id": "bot"}, "body": "reflection",
             "quote": {"id": "forward-parent", "body": "matching payload"}}
    persist(tmp_path, "2", [
        {"ts": 1, "line": "matching payload", "record": {"id": "forward-parent"}},
        {"ts": 2, "line": "reflection", "record": quote}])
    rows = memory.search("matching payload", "1", scope="2")
    assert [row["record"]["id"] for row in rows] == ["quote-parent", "forward-parent"]
    assert rows[0]["record"] == quote
    assert rows[0]["record"]["sender"]["id"] == "bot"


def test_exact_numeric_native_id_lookup_and_typed_identity(memory, tmp_path):
    """Native integer IDs support exact lookup without coercing distinct stored ID types."""
    persist(tmp_path, "2", [
        {"ts": 1, "line": "integer", "record": {"id": 12345}},
        {"ts": 2, "line": "string", "record": {"id": "12345"}},
        {"ts": 3, "line": "not an id", "record": {"id": True}},
        {"ts": 4, "line": "also not an id", "record": {"id": True}}])
    assert [row["record"]["id"] for row in memory.search("12345", "1")] == ["12345", 12345]
    assert memory.search("234", "1", scope="2") == [memory.search("12345", "1")[0]]
    assert len(memory.search("", "1", scope="2")) == 4


def test_pagination_is_after_dedup_and_preserves_archive_bytes(memory, tmp_path):
    """Successive offsets enumerate all unique rows, including equal timestamp ties."""
    persist(tmp_path, "2", [
        {"ts": index // 2, "line": "match", "record": {"id": str(index)}}
        for index in range(12)] + [{"ts": 999, "line": "match", "record": {"id": "0"}}])
    before = (tmp_path / "2.jsonl").read_bytes()
    all_rows = memory.search("match", "1", scope="2", limit=100)
    paged = [row for offset in range(0, 15, 3)
             for row in memory.search("match", "1", scope="2", limit=3, offset=offset)]
    assert paged == all_rows
    assert len(paged) == 12
    assert memory.search("match", "1", scope="2", offset=99) == []
    assert (tmp_path / "2.jsonl").read_bytes() == before


@pytest.mark.parametrize("offset", [-1, True, 1.5, "1", None])
def test_invalid_offset_is_rejected(memory, offset):
    """Invalid offsets fail before archive access instead of silently changing slices."""
    with pytest.raises(ValueError, match="offset"):
        memory.search("match", "1", offset=offset)


def test_search_and_timeline_share_finite_timestamp_order(memory, tmp_path):
    """Malformed or absent times stay unknown and cannot outrank actual dated records."""
    timestamps = [None, "UNKNOWN", True, float("nan"), float("inf"), float("-inf"), -3, 0, 5]
    persist(tmp_path, "2", [
        {"ts": timestamp, "line": "match-" + str(index)}
        for index, timestamp in enumerate(timestamps)])
    rows = memory.search("match", "1", scope="2", limit=20)
    assert [row["line"] for row in rows] == [
        "match-8", "match-7", "match-6", "match-0", "match-1", "match-2",
        "match-3", "match-4", "match-5"]
    assert rows[4]["ts"] == "UNKNOWN"
    timeline = memory.unified_snapshot(limit=20)
    assert [row["line"] for row in timeline["records"]] == [
        "match-0", "match-1", "match-2", "match-3", "match-4", "match-5",
        "match-6", "match-7", "match-8"]
