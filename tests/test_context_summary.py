"""Offline regression tests for effective, checked length-based context summaries."""

import asyncio
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import sys

import pytest

# Import only pure namespace helpers, never the framework-dependent bot runtime.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugin import context_summary  # noqa: E402
from plugin.context_budget import size  # noqa: E402
from plugin.memory import Memory  # noqa: E402


class NativeSummaryPlugin:
    """Record the configured native provider contract without network or tool effects."""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [("Concise persisted continuity summary", [])])

    async def _llm_tools(self, system, prompt, tools, messages, allow_tools):
        """Capture detached summary requests and return a deterministic provider reply."""
        self.calls.append({"system": system, "prompt": json.loads(prompt),
                           "tools": deepcopy(tools), "messages": deepcopy(messages),
                           "allow_tools": allow_tools})
        answer = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(answer, Exception):
            raise answer
        return answer


def native_rows(count=10, *, scope="1", start=0, width=450):
    """Supply exact chronological source identities with enough length to evict a prefix."""
    return [{"scope": scope, "origin_scope": scope, "ts": index,
             "line": "message " + str(index) + " " + "x" * width,
             "record": {"id": index, "sender_id": "native-sender"}}
            for index in range(start, start + count)]


def prepare(plugin, rows, root, *, scope="1", maximum=4096, summary_maximum=1200):
    """Execute one pure compaction pass using deterministic temporary persistence."""
    return asyncio.run(context_summary.prepare(plugin, scope, rows, maximum,
                                              summary_maximum, root=root))


def read_store(root):
    """Read checked durable state independently from the compactor's in-memory object."""
    return json.loads((root / "memory" / "memory.json").read_text(encoding="utf-8"))


def fingerprint(rows):
    """Compute the documented exact compact-JSON prefix hash independently."""
    encoded = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_no_eviction_never_calls_model_or_creates_store(tmp_path):
    """A short source remains intact without generating or persisting unnecessary summaries."""
    plugin = NativeSummaryPlugin()
    rows = native_rows(2)
    before = deepcopy(rows)
    selected, coverage, state = prepare(plugin, rows, tmp_path)
    assert selected == rows == before
    assert coverage["omitted"] == 0
    assert state["status"] == "not_needed"
    assert state["generated"] is False
    assert state["summaries"] == []
    assert plugin.calls == []
    assert not (tmp_path / "memory" / "memory.json").exists()


def test_eviction_generates_checked_summary_and_rehydrates_state(tmp_path):
    """A real native-provider result becomes durable and actually reaches returned context state."""
    plugin = NativeSummaryPlugin()
    rows = native_rows()
    before = deepcopy(rows)
    selected, coverage, state = prepare(plugin, rows, tmp_path)
    omitted = rows[:coverage["omitted"]]
    assert 0 < len(omitted) < len(rows)
    assert selected == rows[len(omitted):]
    assert size(selected) <= 4096
    assert rows == before
    assert plugin.calls
    assert [row for call in plugin.calls for row in call["prompt"]["records"]] == omitted
    assert all(call["allow_tools"] is False and call["tools"] == [] for call in plugin.calls)
    call = plugin.calls[0]
    assert call["allow_tools"] is False
    assert call["tools"] == []
    assert call["messages"] == [{"role": "system", "content": call["system"]},
                                 {"role": "user", "content": json.dumps(
                                     call["prompt"], ensure_ascii=False, separators=(",", ":"))}]
    assert call["prompt"]["records"] == omitted[:len(call["prompt"]["records"])]
    assert call["prompt"]["previous_summary"] == ""
    assert call["prompt"]["trust"] == "untrusted_data"
    assert call["prompt"]["max_summary_chars"] == 688
    saved = read_store(tmp_path)["scopes"]["1"]
    assert saved["summary"] == "Concise persisted continuity summary"
    assert saved["context_compaction"] == {
        "covered_rows": len(omitted), "covered_sha256": fingerprint(omitted),
        "partial_chars": 0, "partial_sha256": None,
        "unit": "serialized_unicode_characters"}
    assert state["status"] == "updated"
    assert state["generated"] is True
    assert state["archive_preserved"] is True
    assert state["summaries"] == [{"scope": "1", "text": saved["summary"],
                                    "updated_at": saved["updated_at"], "lossy": True,
                                    "source": "model_generated_summary", "trust": "untrusted_data"}]
    assert size(state["summaries"]) <= 1200


def test_restart_reuses_validated_prefix_without_model_or_rewrite(tmp_path):
    """A fresh module reads the persisted cursor and summary instead of re-summarizing old input."""
    rows = native_rows()
    first = prepare(NativeSummaryPlugin(), rows, tmp_path)
    before = (tmp_path / "memory" / "memory.json").read_bytes()
    importlib.reload(context_summary)
    restarted = NativeSummaryPlugin()
    selected, coverage, state = prepare(restarted, rows, tmp_path)
    assert restarted.calls == []
    assert selected == first[0]
    assert coverage == first[1]
    assert state["status"] == "loaded"
    assert state["generated"] is False
    assert state["summaries"] == first[2]["summaries"]
    assert (tmp_path / "memory" / "memory.json").read_bytes() == before


def test_newly_omitted_rows_only_extend_existing_summary(tmp_path):
    """Appending evidence summarizes just new omitted rows and includes the prior durable summary."""
    rows = native_rows()
    _, first_coverage, _ = prepare(NativeSummaryPlugin(), rows, tmp_path)
    old_summary = read_store(tmp_path)["scopes"]["1"]["summary"]
    rows.extend(native_rows(1, start=10))
    plugin = NativeSummaryPlugin([("Incrementally updated summary", [])])
    _, coverage, state = prepare(plugin, rows, tmp_path)
    assert coverage["omitted"] > first_coverage["omitted"]
    assert len(plugin.calls) == 1
    assert plugin.calls[0]["prompt"]["records"] == rows[
        first_coverage["omitted"]:coverage["omitted"]]
    assert plugin.calls[0]["prompt"]["previous_summary"] == old_summary
    saved = read_store(tmp_path)["scopes"]["1"]
    assert saved["context_compaction"]["covered_rows"] == coverage["omitted"]
    assert saved["context_compaction"]["covered_sha256"] == fingerprint(rows[:coverage["omitted"]])
    assert state["summaries"][0]["text"] == "Incrementally updated summary"


@pytest.mark.parametrize("reply", [
    ("", []), ("   \n", []), ("x" * 689, []), ("SILENT", []),
    ("must not persist", [{"name": "send_message", "arguments": {}}]),
    RuntimeError("provider unavailable"),
])
def test_invalid_or_failed_generation_preserves_old_summary_and_cursor(tmp_path, reply):
    """Empty, oversize, tool-requesting and failed model results cannot advance durable progress."""
    rows = native_rows()
    prepare(NativeSummaryPlugin(), rows, tmp_path)
    before = (tmp_path / "memory" / "memory.json").read_bytes()
    old = read_store(tmp_path)["scopes"]["1"]
    rows.extend(native_rows(1, start=10))
    plugin = NativeSummaryPlugin([reply])
    _, _, state = prepare(plugin, rows, tmp_path)
    assert len(plugin.calls) == 1
    assert state["status"] == "summary_failed"
    assert state["generated"] is False
    assert (tmp_path / "memory" / "memory.json").read_bytes() == before
    assert read_store(tmp_path)["scopes"]["1"] == old
    assert state["summaries"][0]["text"] == old["summary"]


def test_checked_save_failure_keeps_cursor_and_unvalidated_summary_out_of_context(tmp_path, monkeypatch):
    """A disk failure is not success: neither durable nor returned summary may advance."""
    rows = native_rows()
    prepare(NativeSummaryPlugin(), rows, tmp_path)
    before = (tmp_path / "memory" / "memory.json").read_bytes()
    old = read_store(tmp_path)["scopes"]["1"]
    rows.extend(native_rows(1, start=10))

    def fail_save(self):
        """Simulate an atomic storage failure after the in-memory mutation starts."""
        raise OSError("simulated failed durable replacement")

    monkeypatch.setattr(context_summary.Memory, "save_checked", fail_save)
    _, _, state = prepare(NativeSummaryPlugin([("unpersisted must never enter context", [])]),
                          rows, tmp_path)
    assert state["status"] == "summary_failed"
    assert state["generated"] is False
    assert (tmp_path / "memory" / "memory.json").read_bytes() == before
    assert read_store(tmp_path)["scopes"]["1"] == old
    assert state["summaries"][0]["text"] == old["summary"]


@pytest.mark.parametrize("corrupt", ["{bad", "[]", '{"scopes": []}',
                                     '{"scopes": {"1": null}}', '{"scopes": {"1": "bad"}}',
                                     '{"scopes": {"1": {"turns": "bad"}}}',
                                     '{"scopes": {"1": {"turns": -1}}}',
                                     '{"scopes": {"1": {"turns": true}}}',
                                     '{"scopes": {"1": {"summary": []}}}',
                                     '{"scopes": {"1": {"context_compaction": null}}}',
                                     '{"scopes": {"1": {"context_compaction": []}}}',
                                     '{"scopes": {"1": {"context_compaction": "bad"}}}'])
def test_corrupt_memory_is_not_overwritten_or_summarized(tmp_path, corrupt):
    """Invalid durable schema must not silently become a new empty memory store."""
    path = tmp_path / "memory" / "memory.json"
    path.parent.mkdir()
    path.write_text(corrupt, encoding="utf-8")
    before = path.read_bytes()
    plugin = NativeSummaryPlugin()
    _, coverage, state = prepare(plugin, native_rows(), tmp_path)
    assert coverage["omitted"] > 0
    assert state["status"] == "memory_unavailable"
    assert state["summaries"] == []
    assert state["generated"] is False
    assert plugin.calls == []
    assert path.read_bytes() == before


def test_public_scopes_summarized_separately_and_private_never_leaks(tmp_path):
    """Explicit public sharing cannot mix sender scopes or inject foreign private summaries."""
    memory = Memory(str(tmp_path / "memory" / "memory.json"))
    memory.set_summary("private:7", "private secret must stay out", checked=True)
    memory.set_summary("private_7", "filename alias secret must stay out", checked=True)
    rows = native_rows(2, scope="2", start=0, width=200)
    rows += native_rows(2, scope="3", start=2, width=200)
    rows += native_rows(1, scope="private:7", start=4, width=200)
    rows += native_rows(1, scope="private_7", start=5, width=200)
    rows += native_rows(7, scope="1", start=6)
    plugin = NativeSummaryPlugin()
    _, coverage, state = prepare(plugin, rows, tmp_path)
    assert coverage["omitted"] >= 6
    assert {call["prompt"]["scope"] for call in plugin.calls} == {"2", "3"}
    for call in plugin.calls:
        assert {row["scope"] for row in call["prompt"]["records"]} == {call["prompt"]["scope"]}
        assert "secret" not in json.dumps(call)
    assert {item["scope"] for item in state["summaries"]} == {"2", "3"}
    assert "private secret" not in json.dumps(state)
    saved = read_store(tmp_path)["scopes"]
    assert saved["private:7"]["summary"] == "private secret must stay out"
    assert saved["private_7"]["summary"] == "filename alias secret must stay out"


def test_large_backlog_is_fully_compacted_in_bounded_chunks(tmp_path):
    """One pass drains every evicted record without dropping old evidence or exceeding native input."""
    rows = native_rows(40, width=500)
    plugin = NativeSummaryPlugin()
    _, coverage, state = prepare(plugin, rows, tmp_path)
    omitted = rows[:coverage["omitted"]]
    assert len(plugin.calls) > 2
    assert all(call["allow_tools"] is False and call["tools"] == [] for call in plugin.calls)
    assert all(size(call["messages"]) < 4096 for call in plugin.calls)
    assert [row for call in plugin.calls for row in call["prompt"]["records"]] == omitted
    saved = read_store(tmp_path)["scopes"]["1"]
    assert saved["context_compaction"]["covered_rows"] == len(omitted)
    assert saved["context_compaction"]["covered_sha256"] == fingerprint(omitted)
    assert state["generated"] is True
    assert state["status"] == "updated"
    assert state["summaries"][0]["text"] == saved["summary"]


def test_enormous_record_fragments_keep_identity_and_exact_full_coverage(tmp_path):
    """Derived fragments are marked excerpts and concatenate to the original unchanged JSON evidence."""
    rows = native_rows(1, width=6000)
    rows += native_rows(1, start=1, width=100)
    original = deepcopy(rows)
    plugin = NativeSummaryPlugin()
    _, coverage, state = prepare(plugin, rows, tmp_path)
    assert coverage["omitted"] == 1
    assert len(plugin.calls) > 2
    fragments = [item for call in plugin.calls for item in call["prompt"]["records"]]
    raw = json.dumps(rows[0], ensure_ascii=False, separators=(",", ":"))
    assert all(fragment["kind"] == "source_json_fragment" for fragment in fragments)
    assert all(fragment["source_sha256"] == fingerprint(rows[0]) for fragment in fragments)
    assert all(fragment["not_an_independent_message"] is True for fragment in fragments)
    assert all(fragment["total_chars"] == len(raw) for fragment in fragments)
    assert [fragment["offset"] for fragment in fragments] == [
        sum(len(item["text"]) for item in fragments[:index]) for index in range(len(fragments))]
    assert "".join(fragment["text"] for fragment in fragments) == raw
    assert rows == original
    saved = read_store(tmp_path)["scopes"]["1"]["context_compaction"]
    assert saved["covered_rows"] == 1
    assert saved["covered_sha256"] == fingerprint(rows[:1])
    assert saved["partial_chars"] == 0
    assert state["summaries"]


def test_partial_first_record_restart_keeps_saved_summary_and_offset(tmp_path):
    """A durable partial source cursor resumes with its summarized prefix, not just its text offset."""
    rows = native_rows(1, width=6000) + native_rows(1, start=1, width=100)
    interrupted = NativeSummaryPlugin([("saved first fragment meaning", []),
                                       RuntimeError("interrupted during next fragment")])
    _, _, state = prepare(interrupted, rows, tmp_path)
    assert state["status"] == "summary_failed"
    saved = read_store(tmp_path)["scopes"]["1"]
    cursor = saved["context_compaction"]
    assert cursor["covered_rows"] == 0
    assert 0 < cursor["partial_chars"] < size(rows[0])
    restarted = NativeSummaryPlugin()
    _, _, resumed = prepare(restarted, rows, tmp_path)
    assert restarted.calls[0]["prompt"]["previous_summary"] == saved["summary"]
    assert restarted.calls[0]["prompt"]["records"][0]["offset"] == cursor["partial_chars"]
    assert read_store(tmp_path)["scopes"]["1"]["context_compaction"]["covered_rows"] == 1
    assert resumed["status"] == "updated"


def test_escape_heavy_summary_stays_effective_inside_serialized_budget(tmp_path):
    """A valid escaped model summary is projected, not wholly lost after durable cursor advancement."""
    text = '\\"' * 300
    plugin = NativeSummaryPlugin([(text, [])])
    rows = native_rows()
    _, coverage, state = prepare(plugin, rows, tmp_path)
    saved = read_store(tmp_path)["scopes"]["1"]
    assert saved["summary"] == text
    assert saved["context_compaction"]["covered_rows"] == coverage["omitted"]
    assert state["status"] == "updated" and state["generated"] is True
    assert state["summaries"]
    projected = state["summaries"][0]
    assert projected["text"] and text.startswith(projected["text"])
    assert projected["projection_truncated"] is True
    assert projected["original_chars"] == len(text)
    assert size(state["summaries"]) <= 1200


def test_large_configured_budget_never_exceeds_memory_summary_storage_cap(tmp_path):
    """A context limit above the default must not permit silently truncated durable summaries."""
    rows = native_rows(1, width=1100000)
    plugin = NativeSummaryPlugin()
    _, coverage, state = prepare(plugin, rows, tmp_path, maximum=1000000,
                                 summary_maximum=150000)
    assert coverage["omitted"] == 1
    assert plugin.calls
    assert all(call["prompt"]["max_summary_chars"] <= 100000 for call in plugin.calls)
    assert state["summaries"]
    assert size(state["summaries"]) <= 150000
    assert read_store(tmp_path)["scopes"]["1"]["context_compaction"]["covered_rows"] == 1


def test_many_tiny_rows_use_length_not_an_old_count_trigger(tmp_path):
    """Five hundred tiny records remain intact without count-based summarization."""
    rows = [{"scope": "1", "record": {"id": index}, "line": "雪 "} for index in range(500)]
    plugin = NativeSummaryPlugin()
    selected, coverage, state = prepare(plugin, rows, tmp_path, maximum=size(rows))
    assert selected == rows
    assert len(selected) == 500
    assert coverage["used_chars"] == size(rows)
    assert coverage["omitted"] == 0
    assert state["summaries"] == []
    assert plugin.calls == []
