"""Pure provenance summaries preserve autonomy and never infer legacy identity."""
import copy
import importlib.util
from pathlib import Path


_spec = importlib.util.spec_from_file_location(
    "isolated_conversation_state", Path(__file__).resolve().parents[1] / "plugin" / "conversation_state.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
snapshot = _module.snapshot


def test_shared_history_does_not_become_current_scope_activity():
    """Other-group recent speech cannot acquire the destination group's timestamp."""
    history = [{"scope": "other", "ts": 200, "line": "arbitrary text"}]
    own = [{"scope": "here", "ts": 100, "line": "legacy text"}]
    result = snapshot("here", history, [], {},
                      {"kind": "autonomous_opportunity"}, scope_history=own, now=300)
    assert result["turn_kind"] == "autonomous_opportunity"
    assert result["current_input_count"] == 0
    assert result["current_message_ids"] == []
    assert result["history_scope_counts"][0]["scope"] == "other"
    assert result["current_scope_last_activity"]["ts"] == 100
    assert result["current_scope_last_activity"]["age_seconds"] == 200
    assert result["current_scope_last_known_input"] is None


def test_legacy_lines_never_create_speakers_timestamps_or_scope():
    """Bot-looking prose and missing source metadata stay unknown."""
    result = snapshot("42", ["彼岸酱：hello", {"line": "member：hello"}], [], {})
    assert result["history_scope_counts"] == []
    assert result["history_unknown_scope_count"] == 2
    assert result["current_scope_last_activity"] is None
    assert result["turn_kind"] == "unspecified"


def test_native_kinds_remain_separate_without_text_copies_or_mutation():
    """Known input and assistant deliveries retain factual timestamps, not wording."""
    history = [
        {"origin_scope": "42", "ts": 10, "record": {
            "id": "input-1", "sender": {"id": "user"}, "body": "PRIVATE INPUT"}},
        {"scope": "42", "ts": 20, "record": {
            "kind": "assistant_delivery", "body": "PRIVATE OUTPUT"}},
        {"scope": "42", "ts": 30, "line": "unknown later entry"},
    ]
    messages = [{"id": "input-2", "body": "CURRENT PRIVATE"}]
    before = copy.deepcopy((history, messages))
    result = snapshot("42", history, messages, {"now": "not an input timestamp"},
                      deliveries=[{}, {}], now=40)
    assert result["turn_kind"] == "foreground"
    assert result["current_message_ids"] == ["input-2"]
    assert result["current_scope_last_activity"]["ts"] == 30
    assert result["current_scope_last_known_input"]["ts"] == 10
    assert result["current_scope_last_known_assistant_output"]["ts"] == 20
    assert result["observed_delivery_count"] == 2
    assert result["coverage"]["scope_unknown_kind_count"] == 1
    assert "PRIVATE" not in repr(result)
    assert (history, messages) == before


def test_invalid_times_and_explicit_other_scope_are_not_reassigned():
    """Nonfinite/bool clocks and contradictory origins cannot create fake recency."""
    rows = [{"scope": "42", "ts": float("nan")}, {"scope": "42", "ts": True}]
    result = snapshot("42", rows, [], {}, now=float("inf"), scope_history=[
        {"scope": "other", "ts": 999}, {"ts": 12, "line": "stored locally"}])
    assert result["history_scope_counts"][0]["last_event_at"] is None
    assert result["current_scope_last_activity"]["ts"] == 12
    assert result["current_scope_last_activity"]["age_seconds"] is None
    assert result["coverage"]["scope_record_count"] == 1


def test_no_threshold_or_completion_policy_is_added():
    """Future times and repeated deliveries are observations, never reply decisions."""
    result = snapshot("42", [{"scope": "42", "ts": 100}], [], {},
                      task={}, now=50, deliveries=[{"text": "same"}, {"text": "same"}])
    assert result["turn_kind"] == "task"
    assert result["current_scope_last_activity"]["age_seconds"] == -50
    assert result["observed_delivery_count"] == 2
    assert not ({"reply", "finish", "completed", "should_respond"} & result.keys())
