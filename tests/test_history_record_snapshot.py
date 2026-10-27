"""Offline single-read recorder snapshot contracts, without bot or network imports."""

import builtins
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def recorder(tmp_path):
    """Load the production recorder directly and confine persistence to temporary files."""
    path = Path(__file__).resolve().parents[1] / "plugin" / "history.py"
    spec = importlib.util.spec_from_file_location("snapshot_history", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Recorder(str(tmp_path))


def test_count_all_eligible_before_tail_and_read_once(recorder, tmp_path, monkeypatch):
    """An excluded old row and invalid rows cannot shift the eligible full-file count."""
    items = [{"line": f"row-{index}", "record": {"id": index}} for index in range(30)]
    path = tmp_path / "42.jsonl"
    path.write_text("\n".join(json.dumps(item) for item in items) + '\n{broken\n[]\n', encoding="utf-8")
    original_open = builtins.open
    opened = []

    def counted_open(*args, **kwargs):
        """Observe physical reads without replacing the actual JSONL decoding path."""
        opened.append(args[0])
        return original_open(*args, **kwargs)

    monkeypatch.setattr(builtins, "open", counted_open)
    snapshot = recorder.load_record_snapshot("42", limit=3, exclude_message_ids=(0, 28))
    assert snapshot == {
        "records": [{"scope": "42", "ts": None, **items[index]} for index in (26, 27, 29)],
        "count": 28, "available": True,
    }
    assert opened == [str(path)]


def test_exact_acceptance_unicode_and_unterminated_final_line(recorder, tmp_path):
    """Retain legacy whitespace, source order, nested unknown fields and final UTF-8 rows."""
    metadata = {"id": "文字", "unknown": {"z": [1, {"未来": True}]}, "quote": None}
    valid = [{"ts": 9, "line": "  原文\n🙂  ", "record": metadata, "ignored": 7},
             {"line": " "}, {"ts": None, "line": "末尾", "record": ["unknown", None]}]
    invalid = ["", "  ", "{bad", "null", "[]", '"string"', "7", "true",
               '{}', '{"line": null}', '{"line": 1}', '{"line": false}', '{"line": ""}']
    text = "\n".join(invalid + [json.dumps(item, ensure_ascii=False) for item in valid])
    (tmp_path / "42.jsonl").write_text(text, encoding="utf-8")
    expected = [{"scope": "42", "ts": item.get("ts"), "line": item["line"],
                 **({"record": item["record"]} if "record" in item else {})} for item in valid]
    assert recorder.load_record_snapshot("42", None) == {
        "records": expected, "count": 3, "available": True,
    }
    assert recorder.load_records("42", None) == expected


def test_exclusions_use_native_ids_not_text_or_other_metadata(recorder):
    """String and integer IDs differ; legacy text, message_id and nested IDs are not guessed."""
    entries = [("string", {"id": "7"}), ("integer", {"id": 7}),
               ("legacy id=7", None), ("other-key", {"message_id": "7"}),
               ("nested", {"quote": {"id": "7"}}), ("non-dict", ["7"])]
    for line, metadata in entries:
        recorder.append("42", line, record=metadata)
    string_result = recorder.load_record_snapshot("42", None, exclude_message_ids=("7",))
    assert [row["line"] for row in string_result["records"]] == [line for line, _ in entries[1:]]
    assert string_result["count"] == 5
    integer_result = recorder.load_record_snapshot("42", None, exclude_message_ids=(7,))
    assert [row["line"] for row in integer_result["records"]] == [entries[0][0]] + [line for line, _ in entries[2:]]
    assert integer_result["count"] == 5


def test_scope_isolation_and_original_scope_label(recorder):
    """Exclusions affect only the selected file and rows retain the exact requested scope."""
    for scope in (42, "43", "private:7"):
        recorder.append(scope, "same ID", record={"id": "7"})
    assert recorder.load_record_snapshot(42, exclude_message_ids=("7",)) == {
        "records": [], "count": 0, "available": True,
    }
    assert recorder.load_record_snapshot("43")["count"] == 1
    assert recorder.load_record_snapshot("private:7")["records"][0]["scope"] == "private:7"
    assert recorder.load_record_snapshot(42)["records"][0]["scope"] == "42"


def test_missing_empty_and_directory_availability(recorder, tmp_path):
    """An unreadable/missing store differs from a readable empty file, without creating files."""
    assert recorder.load_record_snapshot("missing") == {"records": [], "count": 0, "available": False}
    assert not (tmp_path / "missing.jsonl").exists()
    (tmp_path / "empty.jsonl").write_text("", encoding="utf-8")
    assert recorder.load_record_snapshot("empty") == {"records": [], "count": 0, "available": True}
    (tmp_path / "directory.jsonl").mkdir()
    assert recorder.load_record_snapshot("directory") == {"records": [], "count": 0, "available": False}
    assert recorder.load_records("missing") == []


@pytest.mark.parametrize("limit", [0, -1])
def test_nonpositive_limit_requests_no_count_and_no_io(recorder, monkeypatch, limit):
    """Both APIs preserve the immediate return even when the scope path cannot be read."""
    def forbidden_path(scope):
        """Fail if either API attempts path resolution rather than returning immediately."""
        pytest.fail("nonpositive limit must not perform path I/O")

    monkeypatch.setattr(recorder, "_path", forbidden_path)
    assert recorder.load_record_snapshot("missing", limit) == {"records": [], "count": 0, "available": True}
    assert recorder.load_records("missing", limit) == []


def test_invalid_utf8_discards_even_previously_decoded_rows(recorder, tmp_path):
    """A later decoding failure invalidates the entire count/tail, like legacy load_records."""
    # Exceed the text decoder's buffer so valid rows can be consumed before the bad byte.
    good = (json.dumps({"line": "valid"}) + "\n").encode("utf-8") * 2000
    (tmp_path / "42.jsonl").write_bytes(good + b"\xff\n")
    assert recorder.load_record_snapshot("42", 2) == {"records": [], "count": 0, "available": False}
    assert recorder.load_records("42", 2) == []


def test_oserror_after_partial_read_discards_snapshot(recorder, monkeypatch):
    """A midstream OS failure must never expose a plausible but partial count."""
    class BrokenRead:
        """Yield one valid row before simulating an interrupted file read."""
        def __enter__(self):
            """Expose the controlled iterator as a file context."""
            return self

        def __exit__(self, *args):
            """Allow the production handler to receive the read exception."""
            return False

        def __iter__(self):
            """Fail only after one source record was already accepted."""
            yield '{"line": "valid"}\n'
            raise OSError("interrupted read")

    def broken_open(*args, **kwargs):
        """Replace only file access, leaving production parsing and cleanup intact."""
        return BrokenRead()

    monkeypatch.setattr(builtins, "open", broken_open)
    assert recorder.load_record_snapshot("42") == {"records": [], "count": 0, "available": False}
    assert recorder.load_records("42") == []


def test_returned_nested_metadata_is_detached_from_disk_and_later_reads(recorder, tmp_path):
    """Avoiding deepcopy on fresh JSON trees never shares mutable state between reads."""
    recorder.append("42", "original", record={"id": "7", "nested": [{"value": "original"}]})
    path = tmp_path / "42.jsonl"
    before = path.read_bytes()
    first = recorder.load_record_snapshot("42")
    second = recorder.load_record_snapshot("42")
    first["records"][0]["record"]["nested"][0]["value"] = "changed"
    first["records"][0]["line"] = "changed"
    assert second == recorder.load_record_snapshot("42")
    assert path.read_bytes() == before
    assert second["records"][0]["record"]["nested"][0]["value"] == "original"


def test_load_records_delegates_with_original_defaults(recorder, monkeypatch):
    """Keep the historical API as a thin projection of the one reader implementation."""
    expected = [{"line": "sentinel"}]
    calls = []

    def snapshot(scope, limit=20, *, exclude_message_ids=()):
        """Record delegate arguments without performing a second parsing implementation."""
        calls.append((scope, limit, exclude_message_ids))
        return {"records": expected, "count": 99, "available": True}

    monkeypatch.setattr(recorder, "load_record_snapshot", snapshot)
    assert recorder.load_records("42") is expected
    assert recorder.load_records("42", None) is expected
    assert calls == [("42", 20, ()), ("42", None, ())]
