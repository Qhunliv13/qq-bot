"""Offline prefix telemetry is content-preserving, private and byte-labelled."""

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugin.conversation_context import build_context  # noqa: E402
from plugin.prefix_metrics import PrefixTracker  # noqa: E402


def request(history=None, scope="private-QQ-123456789", text="now", images=None):
    """Produce the existing native envelope rather than a diagnostic-only format."""
    user = build_context(scope=scope, history=history or [],
                         messages=[{"text": text}], clock={"now": text})
    content = user if images is None else [{"type": "text", "text": user}] + [
        {"type": "image_url", "image_url": {"url": image}} for image in images]
    return {"model": "example", "messages": [
        {"role": "system", "content": "private-persona-secret"},
        {"role": "user", "content": content}],
        "tools": [{"type": "function", "function": {
            "name": "example", "parameters": {"type": "object"}}}]}


def test_first_observation_and_unmodified_payload():
    """Only numeric/boolean fields leave the tracker and payloads remain exact."""
    tracker = PrefixTracker()
    payload = request(["雪🙂" * 300])
    before = deepcopy(payload)
    result = tracker.observe(payload)
    assert payload == before
    assert result["available"] and result["first_observation"]
    assert result["scope_index"] == 1
    assert result["history_prefix_reused_bytes"] == 0
    assert result["input_history_entries"] == 1
    assert result["current_batch_entries"] == 1
    assert all(type(value) in (int, bool) for value in result.values())
    assert result["prefix_resolution_bytes"] == 256


def test_unchanged_and_appended_history_have_certified_byte_prefixes():
    """Appends preserve old leading blocks; JSON closing delimiters may differ."""
    tracker = PrefixTracker()
    history = ["long-history-雪" * 300]
    first = tracker.observe(request(history))
    same = tracker.observe(request(history, text="changed current input"))
    assert same["history_prefix_reused_bytes"] == first["history_bytes"]
    assert same["system_unchanged"] and same["tools_unchanged"]
    assert not same["first_observation"]
    assert same["user_prefix_reused_bytes"] > 256
    appended = tracker.observe(request(history + ["new actual history"], text="third"))
    assert first["history_bytes"] - 256 <= appended["history_prefix_reused_bytes"]
    assert appended["history_prefix_reused_bytes"] < first["history_bytes"]
    assert appended["input_history_entries"] == 2


def test_sliding_history_breaks_prefix_without_reordering_sources():
    """A changed first source is observable instead of disguised as append-only."""
    tracker = PrefixTracker()
    tracker.observe(request(["a" * 700, "b" * 700]))
    result = tracker.observe(request(["b" * 700, "c" * 700]))
    assert result["history_prefix_reused_bytes"] == 0
    assert result["user_prefix_reused_bytes"] < 256


def test_system_tools_and_images_are_compared_independently():
    """Image changes cannot falsely count as unchanged whole-request input."""
    tracker = PrefixTracker()
    payload = request(["history" * 100], images=["data:image/png;base64,SECRET"])
    tracker.observe(payload)
    second = deepcopy(payload)
    second["messages"][0]["content"] += "changed"
    second["tools"][0]["function"]["name"] = "different"
    second["messages"][1]["content"][1]["image_url"]["url"] += "changed"
    result = tracker.observe(second)
    assert not result["system_unchanged"]
    assert not result["tools_unchanged"]
    assert not result["images_unchanged"]
    assert result["image_count"] == 1
    assert result["user_prefix_reused_bytes"] == result["user_bytes"]


def test_scopes_are_isolated_without_identifier_output_or_retention():
    """Two scopes get process-local integers, never raw IDs or public hashes."""
    tracker = PrefixTracker()
    secret = "never-store-this-body-or-url"
    payload = request([secret * 300], scope="secret-scope", images=[secret])
    first = tracker.observe(payload)
    other = tracker.observe(request(["different"], scope="other-secret-scope"))
    again = tracker.observe(payload)
    assert first["scope_index"] == again["scope_index"] == 1
    assert other["scope_index"] == 2 and other["first_observation"]
    assert again["history_prefix_reused_bytes"] == first["history_bytes"]
    retained = repr(tracker.__dict__)
    for value in (secret, "secret-scope", "private-persona-secret", "image_url"):
        assert value not in retained
        assert value not in json.dumps(again)
    assert all(isinstance(key, bytes) for key in tracker._scopes)
    assert all(len(key) == 32 for key in tracker._scopes)


@pytest.mark.parametrize("payload", [None, [], {}, {"messages": []},
    {"messages": [{"role": "user", "content": "not JSON"}]},
    {"messages": [{"role": "user", "content": "{}"}]},
    {"messages": [{"role": "user", "content": '{"conversation":{"scope":"","history":[],"messages":[]}}'}]},
    {"messages": [{"role": "user", "content": [{"type": "text", "text": "{}"},
                                                   {"type": "text", "text": "{}"}]}]},
])
def test_unavailable_never_invents_scope(payload):
    """Ordinary, missing and malformed user inputs do not create fake scopes."""
    tracker = PrefixTracker()
    assert tracker.observe(payload) == {"available": False}
    assert tracker._scopes == {}


def test_partial_tail_and_multibyte_counts_are_bytes_not_characters():
    """A previous partial block is certified against an unchanged current string."""
    tracker = PrefixTracker()
    payload = request(["雪🙂"])
    first = tracker.observe(payload)
    second = tracker.observe(payload)
    assert first["history_bytes"] == len('["雪🙂"]'.encode("utf-8"))
    assert second["history_prefix_reused_bytes"] == first["history_bytes"]
    assert second["user_prefix_reused_bytes"] == first["user_bytes"]


def test_object_key_order_is_canonical_but_array_order_is_significant():
    """Diagnostic schema fingerprints ignore object insertion order, not tools."""
    tracker = PrefixTracker()
    payload = request(["history"])
    tracker.observe(payload)
    payload["tools"] = [{"function": {"parameters": {"type": "object"},
                                        "name": "example"}, "type": "function"}]
    assert tracker.observe(payload)["tools_unchanged"]
    payload["tools"].append({"type": "function", "function": {"name": "other"}})
    assert not tracker.observe(payload)["tools_unchanged"]


def test_new_tracker_does_not_reuse_another_process_observation():
    """Fresh process state cannot imply provider cache is cold or warm."""
    first, second = PrefixTracker(), PrefixTracker()
    payload = request(["history"])
    assert first.observe(payload)["first_observation"]
    assert second.observe(payload)["first_observation"]
    assert first._key != second._key
