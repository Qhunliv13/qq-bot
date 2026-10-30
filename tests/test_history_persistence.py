"""Offline checked-history durability contracts; no bot, credentials or network access."""

import builtins
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def history_module():
    """Load the production recorder without importing the bot or touching configured data."""
    path = Path(__file__).resolve().parents[1] / "plugin" / "history.py"
    spec = importlib.util.spec_from_file_location("checked_history", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def recorder(history_module, tmp_path):
    """Use a temporary explicit archive for every persistence test."""
    return history_module.Recorder(str(tmp_path))


def test_checked_durable_receipt_and_full_metadata(recorder, tmp_path, monkeypatch):
    """Success follows fsync and retains full source bodies beyond the display cap."""
    calls = []
    original_fsync = os.fsync

    def tracked_fsync(fd):
        """Observe durability while still executing the real local file fsync."""
        original_fsync(fd)
        calls.append(fd)

    monkeypatch.setattr(os, "fsync", tracked_fsync)
    body = "完整🙂" * 1500
    record = {"id": "native-1", "body": body, "nested": {"values": [1, True, None]}}
    assert recorder.append_checked("42", body, record=record) == {"persisted": True, "duplicate": False}
    assert len(calls) == 1
    stored = json.loads((tmp_path / "42.jsonl").read_text(encoding="utf-8"))
    assert stored["line"] == body[:2000]
    assert stored["record"] == record
    record["nested"]["values"].append("later")
    assert recorder.load_records("42")[0]["record"]["nested"]["values"] == [1, True, None]


def test_repeated_native_id_leaves_file_unchanged_and_uses_cache(recorder, tmp_path, monkeypatch):
    """A cached exact identity performs no open/write/fsync and does not change archive bytes."""
    record = {"id": "native-1", "body": "original"}
    recorder.append_checked("42", "original", record=record)
    path = tmp_path / "42.jsonl"
    before = path.read_bytes()
    stat_before = path.stat()
    original_open = builtins.open
    opened = []

    def tracked_open(*args, **kwargs):
        """Count disk opens without altering filesystem semantics."""
        opened.append(args[0])
        return original_open(*args, **kwargs)

    monkeypatch.setattr(builtins, "open", tracked_open)
    assert recorder.append_checked("42", "changed", record=record) == {"persisted": True, "duplicate": True}
    assert opened == []
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == stat_before.st_mtime_ns


def test_restart_duplicate_fsyncs_without_rewriting(history_module, recorder, tmp_path):
    """An uncached duplicate must establish durability, without a second row or byte rewrite."""
    recorder.append_checked("42", "original", record={"id": 7})
    path = tmp_path / "42.jsonl"
    before = path.read_bytes()
    restarted = history_module.Recorder(str(tmp_path))
    assert restarted.append_checked("42", "ignored", record={"id": 7}) == {"persisted": True, "duplicate": True}
    assert path.read_bytes() == before


def test_scope_kind_source_and_id_type_are_exact_partitions(recorder, tmp_path):
    """Incoming IDs never suppress outbound IDs or IDs belonging to another exact scope/source."""
    records = [{"id": "7"}, {"id": 7}, {"id": "7", "kind": "assistant_delivery"},
               {"id": "7", "source": {"kind": "first"}},
               {"id": "7", "source": {"kind": "second"}}]
    for scope in ("42", "43", "private:7", "private_7"):
        for record in records:
            assert recorder.append_checked(scope, "same text", record=record)["duplicate"] is False
            assert recorder.append_checked(scope, "same text", record=record)["duplicate"] is True
    assert len((tmp_path / "42.jsonl").read_text(encoding="utf-8").splitlines()) == len(records)
    # The legacy filename sanitizer collides; explicit stored scopes still prevent false deduplication.
    assert len((tmp_path / "private_7.jsonl").read_text(encoding="utf-8").splitlines()) == 2 * len(records)


def test_same_text_different_ids_and_no_ids_are_preserved(recorder):
    """Text content is never an idempotence key, even when native identity is unavailable."""
    records = [{"id": "first"}, {"id": "second"}, None, None,
               {"id": None}, {"id": ""}, {"id": False}, {"message_id": "first"}]
    for record in records:
        assert recorder.append_checked("42", "same text", record=record)["duplicate"] is False
    assert len(recorder.load_records("42", None)) == len(records)


@pytest.mark.parametrize("tail", [b'{broken\n', b'{"line":"truncated"}', b'{"line":',
                                   b'[]\n', b'null\n', b'{"line":"bad","record":{"x":NaN}}\n',
                                   b'{"line":"bad","record":{"x":1e999}}\n',
                                   b'{"line":"\\ud800"}\n', b'\xff\n'])
def test_corrupt_or_unterminated_archives_fail_closed(recorder, history_module, tmp_path, tail):
    """Bad suffixes never cause truncation, repair or overwrite of existing valid rows."""
    recorder.append_checked("42", "valid prefix", record={"id": "old"})
    path = tmp_path / "42.jsonl"
    with path.open("ab") as handle:
        handle.write(tail)
    before = path.read_bytes()
    with pytest.raises(history_module.HistoryPersistenceError):
        recorder.append_checked("42", "new", record={"id": "new"})
    assert path.read_bytes() == before
    assert recorder.append("42", "new", record={"id": "new"})["persisted"] is False
    assert path.read_bytes() == before


@pytest.mark.parametrize("record", [{"value": float("nan")}, {"value": float("inf")},
                                    {"body": "\ud800"}, {"bad": object()}])
def test_invalid_metadata_fails_before_creating_archive(recorder, history_module, tmp_path, record):
    """Serialization and UTF-8 validation finish before any append can change the archive."""
    with pytest.raises(history_module.HistoryPersistenceError) as failure:
        recorder.append_checked("42", "private content", record=record)
    assert failure.value.uncertain is False
    assert not (tmp_path / "42.jsonl").exists()
    assert "private content" not in str(failure.value)
    assert str(tmp_path) not in str(failure.value)


def test_open_failure_is_not_success_and_legacy_receipt_is_sanitized(recorder, history_module, monkeypatch):
    """Legacy callers get a bounded status instead of an exception or sensitive OS message."""
    def denied_open(*args, **kwargs):
        """Simulate a filesystem error containing sensitive text and paths."""
        raise OSError("sensitive text C:/secret/history.jsonl")

    monkeypatch.setattr(builtins, "open", denied_open)
    with pytest.raises(history_module.HistoryPersistenceError) as failure:
        recorder.append_checked("42", "sensitive text")
    assert str(failure.value) == "OSError"
    assert recorder.append("42", "sensitive text") == {
        "persisted": False, "error": "OSError", "uncertain": False}


def test_failed_fsync_keeps_bytes_and_retry_confirms_without_duplicate(recorder, history_module, tmp_path, monkeypatch):
    """A complete but unacknowledged row is retained and re-fsynced before duplicate success."""
    original_fsync = os.fsync

    def denied_fsync(fd):
        """Fail durability after the real write has reached the file."""
        raise OSError("sensitive fsync failure")

    monkeypatch.setattr(os, "fsync", denied_fsync)
    with pytest.raises(history_module.HistoryPersistenceError) as failure:
        recorder.append_checked("42", "full body", record={"id": "7"})
    assert failure.value.uncertain is True
    path = tmp_path / "42.jsonl"
    before = path.read_bytes()
    assert len(before.splitlines()) == 1
    assert recorder.append("42", "full body", record={"id": "7"}) == {
        "persisted": False, "error": "OSError", "uncertain": True}
    monkeypatch.setattr(os, "fsync", original_fsync)
    assert recorder.append_checked("42", "full body", record={"id": "7"}) == {
        "persisted": True, "duplicate": True}
    assert path.read_bytes() == before


def test_partial_write_is_uncertain_and_preserved_without_blind_retry(recorder, history_module, tmp_path, monkeypatch):
    """Partial I/O damage stays in place, and the next checked attempt fails closed."""
    original_open = builtins.open

    class PartialWriter:
        """Write a prefix before raising to emulate interrupted append I/O."""
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            """Use the real append file while controlling only its write operation."""
            return self

        def __exit__(self, *args):
            """Close and flush the real file without suppressing write failures."""
            return self.handle.__exit__(*args)

        def fileno(self):
            """Expose the real descriptor for production stat validation."""
            return self.handle.fileno()

        def write(self, data):
            """Leave observable incomplete bytes to prohibit destructive recovery."""
            self.handle.write(data[:12])
            self.handle.flush()
            raise OSError("partial private write")

    def partial_open(*args, **kwargs):
        """Replace append writes only; validation reads remain real."""
        handle = original_open(*args, **kwargs)
        return PartialWriter(handle) if len(args) > 1 and args[1] == "ab" else handle

    recorder.append_checked("42", "good", record={"id": "first"})
    monkeypatch.setattr(builtins, "open", partial_open)
    with pytest.raises(history_module.HistoryPersistenceError) as failure:
        recorder.append_checked("42", "new", record={"id": "second"})
    assert failure.value.uncertain is True
    path = tmp_path / "42.jsonl"
    before = path.read_bytes()
    monkeypatch.setattr(builtins, "open", original_open)
    with pytest.raises(history_module.HistoryPersistenceError) as retry:
        recorder.append_checked("42", "new", record={"id": "second"})
    assert str(retry.value) == "IncompleteArchive"
    assert path.read_bytes() == before
    assert before.startswith(b'{"ts":') and b'"line": "good"' in before


@pytest.mark.parametrize("phase", ["short_write", "flush", "close"])
def test_other_write_failures_are_uncertain(recorder, history_module, tmp_path, monkeypatch, phase):
    """Short writes, flush failures and close failures never publish successful receipts."""
    original_open = builtins.open

    class FailingWriter:
        """Inject one failure into the real append lifecycle."""
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            """Expose the descriptor-backed controlled writer."""
            return self

        def __exit__(self, *args):
            """Close real resources, then optionally fail the final close acknowledgment."""
            result = self.handle.__exit__(*args)
            if phase == "close":
                raise OSError("private close failure")
            return result

        def fileno(self):
            """Keep stat/fsync pointed at the actual temporary archive."""
            return self.handle.fileno()

        def write(self, data):
            """Return a short count only in the short-write case."""
            return self.handle.write(data[:10] if phase == "short_write" else data)

        def flush(self):
            """Flush real bytes before optionally losing the durability acknowledgment."""
            self.handle.flush()
            if phase == "flush":
                raise OSError("private flush failure")

    def failing_open(*args, **kwargs):
        """Leave validation reads unchanged and wrap only append-mode handles."""
        handle = original_open(*args, **kwargs)
        return FailingWriter(handle) if len(args) > 1 and args[1] == "ab" else handle

    monkeypatch.setattr(builtins, "open", failing_open)
    with pytest.raises(history_module.HistoryPersistenceError) as failure:
        recorder.append_checked("42", "private text", record={"id": "native"})
    assert failure.value.uncertain is True
    assert str(failure.value) == "OSError"
    assert (tmp_path / "42.jsonl").stat().st_size > 0


def test_cached_new_rows_do_not_rescan_unchanged_archive(recorder, monkeypatch):
    """A known-good append uses stat/cache rather than rereading all prior JSONL."""
    recorder.append_checked("42", "first", record={"id": "first"})
    original_open = builtins.open
    modes = []

    def tracked_open(*args, **kwargs):
        """Count only actual open modes used by the checked append transaction."""
        modes.append(args[1] if len(args) > 1 else "r")
        return original_open(*args, **kwargs)

    monkeypatch.setattr(builtins, "open", tracked_open)
    assert recorder.append_checked("42", "second", record={"id": "second"})["persisted"] is True
    assert modes == ["ab"]


def test_external_replacement_and_truncation_invalidate_id_cache(recorder, tmp_path):
    """Cached identities cannot suppress records absent after an external archive replacement."""
    recorder.append_checked("42", "first", record={"id": "same"})
    path = tmp_path / "42.jsonl"
    # Explicit external truncation belongs to the test fixture, never the recorder itself.
    path.write_bytes(b"")
    assert recorder.append_checked("42", "again", record={"id": "same"})["duplicate"] is False
    replacement = tmp_path / "replacement.jsonl"
    replacement.write_bytes(b'{"line":"external","record":{"id":"other"}}\n')
    replacement.replace(path)
    assert recorder.append_checked("42", "again", record={"id": "same"})["duplicate"] is False
    assert [row["record"]["id"] for row in recorder.load_records("42", None)] == ["other", "same"]


def test_threads_and_multiple_recorders_serialize_check_append(history_module, tmp_path):
    """Concurrent process threads acknowledge each native incoming record exactly once."""
    recorders = [history_module.Recorder(str(tmp_path)) for _ in range(4)]

    def append_native(index):
        """Race duplicate arrivals across independently cached Recorder instances."""
        return recorders[index % 4].append_checked("42", "body", record={"id": str(index % 12)})

    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(append_native, range(96)))
    assert all(receipt["persisted"] is True for receipt in receipts)
    assert sum(not receipt["duplicate"] for receipt in receipts) == 12
    rows = recorders[0].load_records("42", None)
    assert len(rows) == 12
    assert {row["record"]["id"] for row in rows} == {str(index) for index in range(12)}


def test_legacy_valid_rows_are_preserved_and_deduplicated(recorder, tmp_path):
    """A complete valid pre-upgrade archive can prove literal-scope native identities."""
    path = tmp_path / "42.jsonl"
    before = b'{"line":"legacy","record":{"id":"7"}}\n'
    path.write_bytes(before)
    assert recorder.append_checked("42", "duplicate", record={"id": "7"}) == {
        "persisted": True, "duplicate": True}
    assert path.read_bytes() == before
    assert recorder.append_checked("42", "new", record={"id": "8"})["duplicate"] is False
    assert path.read_bytes().startswith(before)
