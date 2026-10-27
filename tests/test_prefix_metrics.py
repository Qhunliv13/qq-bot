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


def test_observation_component_metrics_are_private_and_order_aware():
    """Distinguish archive churn from history churn without retaining archive text."""
    tracker = PrefixTracker()
    payload = request(["history" * 300])
    envelope = json.loads(payload["messages"][1]["content"])
    envelope["observations"] = [{"id": "never-log-this-id", "result": "private-observation" * 80}]
    payload["messages"][1]["content"] = json.dumps(envelope, ensure_ascii=False)
    first = tracker.observe(payload)
    assert first["observation_count"] == 1
    assert first["observation_bytes"] > 1000
    assert first["observations_before_history"] is True
    assert first["observations_unchanged"] is False
    envelope["conversation"]["history"].append("new row")
    payload["messages"][1]["content"] = json.dumps(envelope, ensure_ascii=False)
    second = tracker.observe(payload)
    assert second["observations_unchanged"] is True
    assert second["user_prefix_reused_bytes"] > 1000
    envelope["observations"][0]["result"] = "changed actual observation"
    payload["messages"][1]["content"] = json.dumps(envelope, ensure_ascii=False)
    third = tracker.observe(payload)
    assert third["observations_unchanged"] is False
    assert "private-observation" not in repr(vars(tracker))
    assert "never-log-this-id" not in json.dumps(third)
    assert all(type(value) in (int, bool) for value in third.values())


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
    assert tracker.observe(payload) == {"available": False, "request_index": 1}
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


def canonical_bytes(value):
    """Match the documented diagnostic encoding, not transport/template bytes."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def common_prefix(left, right):
    """Calculate an independent exact prefix bound for certification assertions."""
    for index, (before, after) in enumerate(zip(left, right)):
        if before != after:
            return index
    return min(len(left), len(right))


@pytest.mark.parametrize("position", [0, 1, 2])
def test_native_envelope_found_among_extra_text_and_images(position):
    """Native provenance identifies the envelope without dropping adjacent parts."""
    payload = request(["history" * 200], images=["https://secret/image"])
    parts = payload["messages"][1]["content"]
    native = parts.pop(0)
    parts.insert(0, {"type": "text", "text": "untrusted provenance before"})
    parts.append({"type": "text", "text": "{malformed extra"})
    parts.insert(position, native)
    before = deepcopy(payload)
    tracker = PrefixTracker()
    first = tracker.observe(payload)
    second = tracker.observe(payload)
    assert first["available"] and second["available"]
    assert payload == before
    assert first["user_bytes"] == len(native["text"].encode("utf-8"))
    assert first["native_message_bytes"] == len(canonical_bytes(parts))
    assert second["native_message_prefix_reused_bytes"] == first["native_message_bytes"]
    assert second["messages_prefix_reused_bytes"] == first["messages_bytes"]
    assert second["image_count"] == 1


def test_provenance_change_is_not_hidden_by_unchanged_envelope():
    """Extra leading text changes actual content reuse, not legacy envelope reuse."""
    payload = request(["history" * 200], images=["https://secret/image"])
    payload["messages"][1]["content"].insert(
        0, {"type": "text", "text": "origin-before-" * 100})
    tracker = PrefixTracker()
    tracker.observe(payload)
    changed = deepcopy(payload)
    changed["messages"][1]["content"][0]["text"] = "changed-origin-" * 100
    result = tracker.observe(changed)
    assert result["user_prefix_reused_bytes"] == result["user_bytes"]
    assert result["history_prefix_reused_bytes"] == result["history_bytes"]
    assert result["native_message_prefix_reused_bytes"] == 0
    assert result["messages_prefix_reused_bytes"] < 256


def test_image_position_and_url_are_part_of_native_content_prefix():
    """Image order changes the whole-content metric while envelope reuse survives."""
    payload = request(["history" * 200], images=["https://secret/image"])
    tracker = PrefixTracker()
    tracker.observe(payload)
    changed = deepcopy(payload)
    changed["messages"][1]["content"].reverse()
    result = tracker.observe(changed)
    assert result["images_unchanged"]
    assert result["user_prefix_reused_bytes"] == result["user_bytes"]
    assert result["native_message_prefix_reused_bytes"] == 0
    changed["messages"][1]["content"][0]["image_url"]["url"] += "changed"
    result = tracker.observe(changed)
    assert not result["images_unchanged"]
    assert result["native_message_prefix_reused_bytes"] == 0


@pytest.mark.parametrize("duplicate_scope", ["same", "different"])
def test_two_valid_native_envelopes_are_ambiguous(duplicate_scope):
    """Even identical native envelopes cannot unambiguously identify one part."""
    payload = request(scope="same", images=[])
    other = request(scope=duplicate_scope)["messages"][1]["content"]
    payload["messages"][1]["content"].append({"type": "text", "text": other})
    tracker = PrefixTracker()
    assert tracker.observe(payload) == {"available": False, "request_index": 1}
    assert not tracker._scopes


@pytest.mark.parametrize("field,value", [
    ("version", 2), ("version", True), ("version", "1"),
    ("source", {"name": "other", "trust": "untrusted_data"}),
    ("source", {"name": "conversation_context", "trust": "trusted"}),
    ("source", {"name": "conversation_context"}), ("source", None),
])
def test_multipart_requires_exact_native_protocol_provenance(field, value):
    """Malformed provenance is additional text, never a second native envelope."""
    payload = request(images=[])
    native = payload["messages"][1]["content"][0]["text"]
    extra = json.loads(native)
    extra[field] = value
    payload["messages"][1]["content"].insert(
        0, {"type": "text", "text": json.dumps(extra)})
    result = PrefixTracker().observe(payload)
    assert result["available"]
    assert result["user_bytes"] == len(native.encode("utf-8"))
    payload["messages"][1]["content"].pop()
    assert not PrefixTracker().observe(payload)["available"]


@pytest.mark.parametrize("multipart", [False, True])
def test_legacy_conversation_only_envelope_remains_supported(multipart):
    """Legacy scope/history/messages works only when no other text competes."""
    text = '{"conversation":{"scope":"legacy","history":[],"messages":[]}}'
    content = [{"type": "text", "text": text}] if multipart else text
    payload = {"messages": [{"role": "user", "content": content}]}
    assert PrefixTracker().observe(payload)["available"]
    payload["messages"][0]["content"] = [
        {"type": "text", "text": text}, {"type": "text", "text": "provenance"}]
    assert not PrefixTracker().observe(payload)["available"]


def test_messages_metric_preserves_roles_order_and_canonicalizes_keys():
    """Full request order is visible even when isolated system content is equal."""
    payload = request(["history" * 200])
    payload["messages"].append({"role": "assistant", "content": "answer" * 200})
    tracker = PrefixTracker()
    first = tracker.observe(payload)
    reordered_keys = deepcopy(payload)
    reordered_keys["messages"] = [dict(reversed(list(message.items())))
                                  for message in payload["messages"]]
    same = tracker.observe(reordered_keys)
    assert same["messages_bytes"] == len(canonical_bytes(payload["messages"]))
    assert same["messages_prefix_reused_bytes"] == first["messages_bytes"]
    moved = deepcopy(payload)
    moved["messages"][0], moved["messages"][1] = moved["messages"][1], moved["messages"][0]
    changed = tracker.observe(moved)
    assert changed["system_unchanged"] and changed["tools_unchanged"]
    assert changed["messages_prefix_reused_bytes"] == 0
    assert changed["user_prefix_reused_bytes"] == changed["user_bytes"]


def test_appended_full_request_prefix_is_a_certified_lower_bound():
    """Appending history and request messages never overstates byte-prefix reuse."""
    payload = request(["long-history-雪" * 300], images=["secret-image"])
    tracker = PrefixTracker()
    tracker.observe(payload)
    appended = request(["long-history-雪" * 300, "new history"], images=["secret-image"])
    appended["messages"].append({"role": "assistant", "content": "new answer"})
    result = tracker.observe(appended)
    for field, old, new in [
        ("native_message_prefix_reused_bytes", payload["messages"][1]["content"],
         appended["messages"][1]["content"]),
        ("messages_prefix_reused_bytes", payload["messages"], appended["messages"]),
    ]:
        exact = common_prefix(canonical_bytes(old), canonical_bytes(new))
        assert exact - 256 < result[field] <= exact
        assert result[field] > 256


def test_request_index_counts_unavailable_calls_across_scopes():
    """Correlation index advances even when no scope or valid serialization exists."""
    tracker = PrefixTracker()
    first = tracker.observe(request(scope="one"))
    unavailable = tracker.observe(None)
    second = tracker.observe(request(scope="two"))
    invalid = request(scope="two")
    invalid["messages"].append({"role": "assistant", "content": float("nan")})
    failed = tracker.observe(invalid)
    again = tracker.observe(request(scope="one"))
    assert [item["request_index"] for item in (first, unavailable, second, failed, again)] == [1, 2, 3, 4, 5]
    assert unavailable == {"available": False, "request_index": 2}
    assert failed == {"available": False, "request_index": 4}
    assert first["scope_index"] == again["scope_index"] == 1
    assert second["scope_index"] == 2
    assert again["global_messages_prefix_reused_bytes"] == 0


def test_global_prefix_uses_only_immediately_previous_request_across_scopes():
    """Routing diagnostics compare adjacent scopes without retaining source data."""
    tracker = PrefixTracker()
    payload = request(["shared history" * 100], scope="secret-scope-one")
    # Common system content gives different native scopes a meaningful leading span.
    payload["messages"][0]["content"] *= 100
    first = tracker.observe(payload)
    other = deepcopy(payload)
    envelope = json.loads(other["messages"][1]["content"])
    envelope["conversation"]["scope"] = "secret-scope-two"
    other["messages"][1]["content"] = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    second = tracker.observe(other)
    assert second["first_observation"] and second["messages_prefix_reused_bytes"] == 0
    assert second["global_messages_prefix_reused_bytes"] > 256
    again = tracker.observe(payload)
    assert again["messages_prefix_reused_bytes"] == first["messages_bytes"]
    assert again["global_messages_prefix_reused_bytes"] < first["messages_bytes"]
    assert again["global_messages_prefix_reused_bytes"] == second["global_messages_prefix_reused_bytes"]
    retained = repr(tracker.__dict__)
    serialized = json.dumps(again)
    for secret in ("secret-scope-one", "secret-scope-two", "private-persona-secret", "shared history"):
        assert secret not in retained and secret not in serialized
    assert all(type(value) in (int, bool) for value in again.values())


def test_first_user_only_is_not_replaced_by_later_native_user():
    """A malformed first user cannot be bypassed by finding a convenient later one."""
    payload = request()
    payload["messages"].insert(1, {"role": "user", "content": "not native"})
    assert PrefixTracker().observe(payload) == {"available": False, "request_index": 1}
