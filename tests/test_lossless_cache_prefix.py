"""Offline before/after evidence: local exact UTF-8 bytes, never provider hits."""

from copy import deepcopy
import json
import os.path
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugin.conversation_context import build_context, system_prompt  # noqa: E402
from plugin.image_transcript import with_images  # noqa: E402


# This is the complete f06174b top-level order, not a sorted approximation.
_BASELINE_ORDER = (
    "version", "source", "observations", "conversation", "other_scopes", "task",
    "attachments", "timing", "memory", "trigger", "activity",
    "conversation_state", "deliveries", "observation_window",
)


def _encode(value):
    """Use the builder's exact JSON encoding without sorting any source values."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _unique_object(pairs):
    """Reject duplicate fields at every level so equality cannot hide data loss."""
    keys = [key for key, _ in pairs]
    assert len(keys) == len(set(keys))
    return dict(pairs)


def _baseline(wire):
    """Reconstruct the old ordering and fail if any unaccounted field is added."""
    parsed = json.loads(wire, object_pairs_hook=_unique_object)
    assert set(parsed) <= set(_BASELINE_ORDER)
    conversation = parsed["conversation"]
    parsed["conversation"] = {key: conversation[key] for key in (
        "history", "scope", "messages", "history_context", "anchor") if key in conversation}
    return _encode({key: parsed[key] for key in _BASELINE_ORDER if key in parsed})


def _prefix(first, second):
    """Measure exact common UTF-8 bytes, which need not end at a character boundary."""
    return len(os.path.commonprefix((first.encode("utf-8"), second.encode("utf-8"))))


def _fixture():
    """Provide synthetic full context with source/media precision and nontrivial order."""
    return {
        "scope": "101",
        "history": [{"scope": "101", "ts": 1793270000.123456,
                     "line": "历史雪\\\"\n" * 16,
                     "record": {"id": index, "sender": {"id": "7", "name": "姓名"},
                                "parts": [{"type": "text", "text": "原文"},
                                          {"type": "image", "url": "https://invalid.example/图.png"}]}}
                    for index in range(240)],
        "messages": [{"id": "m0", "body": "当前原文", "quote": {"id": 0, "body": "引用"}}],
        "clock": {"now": "2026-10-29T23:00:00.123456+08:00", "epoch": 1793270000.123456},
        "observations": [{"id": index, "result": {"z": "观察雪" * 24, "a": False}}
                         for index in range(3)],
        "memory": {"preferences": [{"id": "p0", "text": "偏好原文雪\\\"\n" * 256,
                                    "source": {"scope": "101", "message_ids": [0, "0"]},
                                    "images": ["https://invalid.example/记忆.png"],
                                    "at": 1793270000.123456}],
                   "facts": [{"z": None, "a": False, "empty": [], "number": 9007199254740993}],
                   "scope": "101", "trust": "untrusted_data"},
        "other_scopes": [], "task": {"status": "running", "text": "任务"},
        "image_refs": ["https://invalid.example/当前.png"],
        "attachment_origins": [{"ref": "https://invalid.example/当前.png", "source": {"id": 7}}],
        "anchor_message_id": "m0",
        "history_context": {"mode": "one_shared_timeline",
                            "source": {"name": "unified_group_memory", "trust": "untrusted_data"},
                            "coverage": {"returned": 240, "limit": 240, "more_available": True,
                                         "scope_count": 2, "archive_scopes": ["101", "202"],
                                         "returned_scopes": ["101", "202"], "cache_block_size": 30,
                                         "cache_padding": 0, "cache_alignment_available": True}},
        "trigger": {"kind": "message"}, "activity": {"count": 0},
        "conversation_state": {"elapsed": 0.123456789}, "deliveries": [],
        "observation_window": {"retained": 3, "omitted": 0},
    }


def _identity(ref):
    """Keep synthetic media references exact without network or filesystem access."""
    return ref


def _payload(wire, inputs):
    """Exercise native image assembly while keeping tools and model parameters fixed."""
    messages = [{"role": "system", "content": system_prompt("Synthetic persona")},
                {"role": "user", "content": wire}]
    messages = with_images(messages, inputs["image_refs"], _identity, 10,
                           initial=True, source_records=inputs["attachment_origins"])
    return {"model": "synthetic-model", "temperature": 0.123456789,
            "tools": [{"type": "function", "function": {"name": "z_tool"}},
                      {"type": "function", "function": {"name": "a_tool"}}],
            "messages": messages}


def _scenario(name):
    """Change only the named evidence while also advancing real current/time values."""
    before = _fixture()
    after = deepcopy(before)
    after["messages"][0]["body"] = "下一条原文"
    after["clock"]["epoch"] = 1793270001.987654
    after["clock"]["now"] = "2026-10-29T23:00:01.987654+08:00"
    if name == "history_append":
        # Appended history retains all 240 earlier rows in their exact chronology.
        after["history"].append({"scope": "101", "ts": 1793270001.987654, "line": "追加历史"})
        after["history_context"]["coverage"]["returned"] = 241
    elif name == "memory_edit":
        # Explicitly quantify the cost when early persisted memory itself changes.
        after["memory"]["preferences"][0]["text"] = "修改偏好"
    elif name == "observation_append":
        # Observation-first locality is intentionally unchanged by this patch.
        after["observations"].append({"id": 3, "result": "新观察"})
    elif name == "scope_switch":
        after["scope"] = "202"
        after["memory"]["scope"] = "202"
    elif name in ("coverage_change", "coverage_only"):
        # Archive discovery can change even if the selected history is unchanged.
        after["history_context"]["coverage"]["scope_count"] = 3
        after["history_context"]["coverage"]["archive_scopes"].append("303")
        if name == "coverage_only":
            # Quantify the downside when a current batch is resubmitted unchanged.
            after["messages"] = deepcopy(before["messages"])
            after["clock"] = deepcopy(before["clock"])
    elif name == "image_change":
        after["image_refs"] = ["https://invalid.example/下一张.png"]
        after["attachment_origins"] = [{"ref": after["image_refs"][0], "source": {"id": 8}}]
    return before, after


def compare_scenario(name):
    """Assert exact parsed equivalence and return reproducible local byte measurements."""
    inputs = _scenario(name)
    originals = deepcopy(inputs)
    wires = [build_context(**item) for item in inputs]
    old_wires = [_baseline(wire) for wire in wires]
    old_payloads, new_payloads = [], []
    for item, old, new in zip(inputs, old_wires, wires):
        old_data = json.loads(old, object_pairs_hook=_unique_object)
        new_data = json.loads(new, object_pairs_hook=_unique_object)
        assert old_data == new_data
        assert len(old.encode("utf-8")) == len(new.encode("utf-8"))
        assert new_data["memory"] == item["memory"]
        assert new_data["conversation"]["history"] == item["history"]
        assert new_data["timing"] == item["clock"]
        assert list(new_data["memory"]["facts"][0]) == ["z", "a", "empty", "number"]
        old_payload, new_payload = _payload(old, item), _payload(new, item)
        # Only one text string differs in wire order: all image positions, source
        # labels, tool array ordering, persona and inference parameters are exact.
        normalized = deepcopy(new_payload)
        assert json.loads(normalized["messages"][1]["content"][0]["text"]) == old_data
        normalized["messages"][1]["content"][0]["text"] = old
        assert normalized == old_payload
        assert len(_encode(old_payload).encode("utf-8")) == len(_encode(new_payload).encode("utf-8"))
        old_payloads.append(_encode(old_payload))
        new_payloads.append(_encode(new_payload))
    assert inputs == originals
    before, after = _prefix(*old_wires), _prefix(*wires)
    return {"scenario": name, "baseline_envelope_prefix_bytes": before,
            "optimized_envelope_prefix_bytes": after, "envelope_prefix_gain_bytes": after - before,
            "baseline_payload_prefix_bytes": _prefix(*old_payloads),
            "optimized_payload_prefix_bytes": _prefix(*new_payloads)}


@pytest.mark.parametrize("scenario", ["current_change", "history_append", "image_change",
                                      "memory_edit", "observation_append", "scope_switch",
                                      "coverage_change", "coverage_only"])
def test_lossless_prefix_comparator(scenario):
    """Require concrete gains and honest negative cases, not a provider cache claim."""
    result = compare_scenario(scenario)
    gain = result["envelope_prefix_gain_bytes"]
    if scenario in ("current_change", "image_change", "memory_edit", "scope_switch"):
        assert gain == len((',"history_context":' + _encode(_fixture()["history_context"])).encode("utf-8"))
        assert result["optimized_payload_prefix_bytes"] > result["baseline_payload_prefix_bytes"]
    elif scenario in ("observation_append", "history_append"):
        assert gain == 0
    else:
        # Coverage changes can interrupt metadata, but never the preceding history.
        assert result["optimized_envelope_prefix_bytes"] > 100000
        if scenario == "coverage_only":
            assert gain < 0


@pytest.mark.parametrize("metadata", [None, False, 0, "", [], {}, {"z": None, "a": False}])
def test_optional_history_metadata_defaults_and_values_remain_exact(metadata):
    """Absent metadata preserves wire identity; false-like metadata is never omitted."""
    inputs = _fixture()
    inputs["history_context"] = metadata
    wire = build_context(**inputs)
    parsed = json.loads(wire, object_pairs_hook=_unique_object)
    conversation = parsed["conversation"]
    if metadata is None:
        assert "history_context" not in conversation
        assert wire == _baseline(wire)
    else:
        assert conversation["history_context"] == metadata
        assert list(conversation) == ["history", "history_context", "scope", "messages", "anchor"]


def rejected_memory_candidate(name):
    """Reproduce why moving scope-specific memory ahead of shared history was rejected."""
    old_wires, candidates = [], []
    for inputs in _scenario(name):
        old = _baseline(build_context(**inputs))
        parsed = json.loads(old)
        order = list(_BASELINE_ORDER)
        order.remove("memory")
        order.insert(order.index("conversation"), "memory")
        candidate = _encode({key: parsed[key] for key in order if key in parsed})
        assert json.loads(candidate) == parsed
        old_wires.append(old)
        candidates.append(candidate)
    baseline, candidate = _prefix(*old_wires), _prefix(*candidates)
    return {"scenario": name, "baseline_prefix_bytes": baseline,
            "rejected_candidate_prefix_bytes": candidate, "gain_bytes": candidate - baseline}


def test_scope_specific_memory_stays_after_shared_history():
    """Document the measured regression without enabling the rejected layout."""
    assert rejected_memory_candidate("current_change")["gain_bytes"] > 0
    assert rejected_memory_candidate("scope_switch")["gain_bytes"] < -100000
    assert rejected_memory_candidate("memory_edit")["gain_bytes"] < -100000


if __name__ == "__main__":
    # Numeric synthetic evidence only; no provider call or production content.
    rows = [compare_scenario(name) for name in (
        "current_change", "history_append", "image_change", "memory_edit",
        "observation_append", "scope_switch", "coverage_change", "coverage_only")]
    print(json.dumps({"comparisons": rows, "equal_weight_synthetic_sum": {
        "baseline_envelope_prefix_bytes": sum(row["baseline_envelope_prefix_bytes"] for row in rows),
        "optimized_envelope_prefix_bytes": sum(row["optimized_envelope_prefix_bytes"] for row in rows)},
        "rejected_memory_candidate": [rejected_memory_candidate(name) for name in (
            "current_change", "history_append", "memory_edit", "scope_switch")]},
        ensure_ascii=False, indent=2))
