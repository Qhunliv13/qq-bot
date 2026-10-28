"""Offline exactness contracts for incremental JSONL snapshot caching."""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugin.history import Recorder


def _row(index):
    """Retain native typed IDs and nested source metadata in archive fixtures."""
    return {"ts": index, "line": str(index), "record": {"id": index, "body": str(index)}}


def _append(path, value, newline=True):
    """Append exactly the bytes the real JSONL writer supplies."""
    with path.open("ab") as handle:
        handle.write(json.dumps(value).encode() + (b"\n" if newline else b""))


def test_cached_snapshot_detaches_nested_data_and_skips_rescan(tmp_path, monkeypatch):
    """Stable files reuse parsed rows without sharing mutable native records."""
    recorder = Recorder(str(tmp_path))
    path = tmp_path / "42.jsonl"
    _append(path, _row(1))
    first = recorder.load_record_snapshot("42", 2)
    first["records"][0]["record"]["body"] = "mutated"
    monkeypatch.setattr(recorder, "_scan_snapshot", lambda *args: (_ for _ in ()).throw(AssertionError("rescan")))
    assert recorder.load_record_snapshot("42", 2)["records"][0]["record"]["body"] == "1"


def test_append_reads_only_new_complete_bytes(tmp_path, monkeypatch):
    """An append resumes at the cached newline, retaining full eligible count."""
    recorder = Recorder(str(tmp_path))
    path = tmp_path / "42.jsonl"
    _append(path, _row(1))
    recorder.load_record_snapshot("42", 2)
    offset = path.stat().st_size
    original = recorder._scan_snapshot
    reads = []

    def scan(handle, scope, limit, excluded, state):
        """Record the actual resume position without replacing archive semantics."""
        reads.append(handle.tell())
        return original(handle, scope, limit, excluded, state)

    monkeypatch.setattr(recorder, "_scan_snapshot", scan)
    _append(path, _row(2))
    result = recorder.load_record_snapshot("42", 2)
    assert reads == [offset]
    assert result["count"] == 2
    assert [row["line"] for row in result["records"]] == ["1", "2"]


def test_unterminated_valid_and_partial_rows_are_replayed(tmp_path):
    """A final row counts before newline but never counts twice when completed."""
    recorder = Recorder(str(tmp_path))
    path = tmp_path / "42.jsonl"
    _append(path, _row(1), newline=False)
    assert recorder.load_record_snapshot("42")["count"] == 1
    with path.open("ab") as handle:
        handle.write(b"\n" + b'{"line":"second",')
    assert recorder.load_record_snapshot("42")["count"] == 1
    with path.open("ab") as handle:
        handle.write(b'"record":{"id":2}}\n')
    result = recorder.load_record_snapshot("42")
    assert result["count"] == 2
    assert [row["line"] for row in result["records"]] == ["1", "second"]


def test_limit_exclusion_rewrite_truncate_and_replace(tmp_path):
    """Query changes and nonappend files rebuild with original typed ID equality."""
    recorder = Recorder(str(tmp_path))
    path = tmp_path / "42.jsonl"
    for index in range(5):
        _append(path, _row(index))
    assert len(recorder.load_record_snapshot("42", 1)["records"]) == 1
    assert len(recorder.load_record_snapshot("42", 5)["records"]) == 5
    assert recorder.load_record_snapshot("42", 2, exclude_message_ids=(4,))["count"] == 4
    assert recorder.load_record_snapshot("42", 2, exclude_message_ids=("4",))["count"] == 5
    path.write_bytes(json.dumps(_row(9)).encode() + b"\n")
    assert recorder.load_record_snapshot("42")["count"] == 1
    replacement = tmp_path / "replacement"
    replacement.write_bytes(json.dumps(_row(8)).encode() + b"\n")
    replacement.replace(path)
    assert recorder.load_record_snapshot("42")["records"][0]["line"] == "8"


def test_base_activity_lookup_keeps_filtered_tail(tmp_path, monkeypatch):
    """Base limit-one lookups do not evict the most recent filtered query."""
    recorder = Recorder(str(tmp_path))
    path = tmp_path / "42.jsonl"
    for index in range(5):
        _append(path, _row(index))
    recorder.load_record_snapshot("42", 3, exclude_message_ids=(4,))
    recorder.load_record_snapshot("42", 1)
    monkeypatch.setattr(recorder, "_scan_snapshot", lambda *args: (_ for _ in ()).throw(AssertionError("rescan")))
    assert recorder.load_record_snapshot("42", 3, exclude_message_ids=(4,))["count"] == 4


def test_universal_newlines_and_concurrent_detached_reads(tmp_path):
    """CR/CRLF legacy rows and threaded readers preserve detached exact evidence."""
    from concurrent.futures import ThreadPoolExecutor
    recorder = Recorder(str(tmp_path))
    path = tmp_path / "42.jsonl"
    path.write_bytes(json.dumps(_row(1)).encode() + b"\r" + json.dumps(_row(2)).encode() + b"\r\n")
    assert recorder.load_record_snapshot("42")["count"] == 2
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda unused: recorder.load_record_snapshot("42"), range(9)))
    results[0]["records"][0]["record"]["body"] = "not shared"
    assert all(result["records"][0]["record"]["body"] == "1" for result in results[1:])


def test_invalid_json_and_unicode_preserve_original_contract(tmp_path):
    """Malformed JSON rows skip, decoding failures publish no partial snapshot."""
    recorder = Recorder(str(tmp_path))
    path = tmp_path / "42.jsonl"
    path.write_bytes(b'invalid\n{}\n{"line":""}\n')
    _append(path, _row(1))
    assert recorder.load_record_snapshot("42")["count"] == 1
    with path.open("ab") as handle:
        handle.write(b"\xff\n")
    assert recorder.load_record_snapshot("42") == {"records": [], "count": 0, "available": False}
    assert recorder.load_record_snapshot("42", 0) == {"records": [], "count": 0, "available": True}
