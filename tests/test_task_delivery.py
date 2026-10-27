"""Delivery evidence is pure, typed and conservative, without live plugin imports."""

import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def delivery():
    """Load only the pure ledger rather than starting the plugin runtime."""
    path = Path(__file__).resolve().parents[1] / "plugin" / "task_delivery.py"
    spec = importlib.util.spec_from_file_location("isolated_task_delivery", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fresh_is_independent_and_quiet_tasks_finish(delivery):
    """Each task owns its lists and quiet success never claims outbound evidence."""
    first, second = delivery.fresh(), delivery.fresh()
    assert first == {
        "delivery_attempted": False, "delivery_confirmed": False,
        "delivery_failed": False, "delivery_unknown": False,
        "delivery_message_ids": [], "last_tools": [],
    }
    first["last_tools"].append("read_file")
    first["delivery_message_ids"].append("123")
    assert second["last_tools"] == second["delivery_message_ids"] == []
    assert delivery.can_finish(second) is True
    assert delivery.completion_failure(second) is None


@pytest.mark.parametrize("name", ["read_file", "task_done", "qq_history", "send_email", "qq_send_other", "ETF", None, []])
def test_unreviewed_tools_are_ignored(delivery, name):
    """Business wording and arbitrary result fields cannot invent outbound attempts."""
    outcome = delivery.fresh()
    before = json.loads(json.dumps(outcome))
    delivery.observe(outcome, name, {"ok": True, "delivered": True, "message_id": 123})
    assert outcome == before


@pytest.mark.parametrize("message_id", [123, "123", "00123"])
def test_qq_send_requires_strict_receipt(delivery, message_id):
    """Native delivery needs true flags and preserves valid receipt IDs as strings."""
    outcome = delivery.fresh()
    delivery.observe(outcome, "qq_send", {"ok": True, "delivered": True, "message_id": message_id})
    assert outcome["delivery_confirmed"] is True
    assert outcome["delivery_message_ids"] == [str(message_id)]
    assert delivery.can_finish(outcome) is True


@pytest.mark.parametrize("message_id", [None, True, False, 0, -1, 1.5, "", "0", "000", "-1", "+1", " 1", "1.0", "１２３", {}, []])
def test_qq_send_invalid_receipt_is_unknown(delivery, message_id):
    """Malformed IDs never turn HTTP or adapter success into confirmed delivery."""
    outcome = delivery.fresh()
    delivery.observe(outcome, "qq_send", {"ok": True, "delivered": True, "message_id": message_id})
    assert outcome["delivery_attempted"] is True
    assert outcome["delivery_confirmed"] is False
    assert outcome["delivery_unknown"] is True
    assert outcome["delivery_message_ids"] == []
    assert delivery.can_finish(outcome) is False


@pytest.mark.parametrize("result", [None, [], "sent", {}, {"ok": True},
    {"ok": 1, "delivered": True, "message_id": 123},
    {"ok": "true", "delivered": True, "message_id": 123},
    {"ok": True, "delivered": 1, "message_id": 123},
    {"ok": True, "data": {"message_id": 123}},
    {"ok": True, "delivered": False, "message_id": 123}])
def test_missing_or_truthy_qq_fields_are_not_receipts(delivery, result):
    """Only actual booleans and the reviewed native result envelope count."""
    outcome = delivery.fresh()
    delivery.observe(outcome, "qq_send", result)
    assert outcome["delivery_unknown"] is True
    assert outcome["delivery_confirmed"] is False
    assert delivery.completion_failure(outcome) == "task_delivery_unknown"


@pytest.mark.parametrize("name", ["qq_send", "reply_now", "send_file", "send_shot", "vm_send_shot"])
@pytest.mark.parametrize("code", ["transport_exception", "send_exception", "delivery_not_confirmed"])
def test_unknown_error_codes_do_not_claim_known_failure(delivery, name, code):
    """Timeout/empty acknowledgements may already have sent and must not be retried."""
    outcome = delivery.fresh()
    delivery.observe(outcome, name, {"ok": False, "delivered": False, "error": code})
    assert outcome["delivery_unknown"] is True
    assert outcome["delivery_failed"] is False
    assert delivery.can_finish(outcome) is False


@pytest.mark.parametrize("result", [
    {"ok": False}, {"ok": False, "error": "onebot_action_failed"},
    {"ok": True, "error": "invalid_arguments"},
    {"ok": False, "delivered": True, "message_id": 123},
])
def test_explicit_failure_wins_over_receipt_fields(delivery, result):
    """Contradictory success evidence cannot override an explicit rejected action."""
    outcome = delivery.fresh()
    delivery.observe(outcome, "qq_send", result)
    assert outcome["delivery_failed"] is True
    assert outcome["delivery_confirmed"] is False
    assert delivery.completion_failure(outcome) == "task_delivery_failed"


def test_legacy_transport_exception_is_unknown(delivery):
    """Older native adapters label transport errors as action failures with a class."""
    outcome = delivery.fresh()
    delivery.observe(outcome, "qq_send", {
        "ok": False, "error": "onebot_action_failed", "exception": "TimeoutError"})
    assert outcome["delivery_unknown"] is True
    assert outcome["delivery_failed"] is False


def test_reply_now_silence_is_not_an_attempt(delivery):
    """Intentional empty interim output neither forces outbound work nor blocks finish."""
    outcome = delivery.fresh()
    delivery.observe(outcome, "reply_now", {"ok": True, "delivered": False})
    assert outcome == delivery.fresh()
    assert delivery.can_finish(outcome) is True


@pytest.mark.parametrize("result,confirmed", [
    ({"ok": True, "delivered": True, "text": "actual body"}, True),
    ({"ok": True, "delivered": True, "message_id": "123"}, True),
    ({"ok": True, "delivered": True, "delivery_message_ids": ["123"]}, True),
    ({"ok": True, "delivered": True}, False),
    ({"ok": True, "delivered": True, "text": "  "}, False),
    ({"ok": True, "delivered": True, "text": 123}, False),
    ({"ok": 1, "delivered": False}, False),
])
def test_reply_now_requires_actual_transport_evidence(delivery, result, confirmed):
    """Foreground bodies and task receipts count, never bare optimistic booleans."""
    outcome = delivery.fresh()
    delivery.observe(outcome, "reply_now", result)
    assert outcome["delivery_confirmed"] is confirmed
    assert outcome["delivery_unknown"] is (not confirmed)


@pytest.mark.parametrize("name", ["send_file", "send_shot", "vm_send_shot"])
@pytest.mark.parametrize("result,confirmed", [
    ({"ok": True}, False),
    ({"ok": True, "delivered": True}, True),
    ({"ok": True, "data": {"message_id": 123}}, True),
    ({"ok": True, "data": {"message_id": True}}, False),
    ({"ok": 1, "delivered": True}, False),
])
def test_reviewed_media_needs_evidence_not_root_success(delivery, name, result, confirmed):
    """VM/root ok alone confirms no delivery; explicit fallback or receipt does."""
    outcome = delivery.fresh()
    delivery.observe(outcome, name, result)
    assert outcome["delivery_confirmed"] is confirmed
    assert outcome["delivery_unknown"] is (not confirmed)


@pytest.mark.parametrize("first", [
    {"ok": False, "error": "invalid_arguments"},
    {"ok": False, "error": "delivery_not_confirmed"},
])
def test_partial_success_never_erases_prior_failure(delivery, first):
    """Mixed outcomes pause for review, even when a later send has a receipt."""
    outcome = delivery.fresh()
    delivery.observe(outcome, "qq_send", first)
    delivery.observe(outcome, "qq_send", {"ok": True, "delivered": True, "message_id": 123})
    assert outcome["delivery_confirmed"] is True
    assert delivery.can_finish(outcome) is False
    delivery.observe(outcome, "qq_send", {"ok": False, "error": "delivery_not_confirmed"})
    assert delivery.completion_failure(outcome) == "task_delivery_unknown"


def test_result_is_not_mutated_or_retained(delivery):
    """Only copied ID strings survive; raw bodies/errors and credentials never do."""
    outcome = delivery.fresh()
    outcome["last_tools"].append("reply_now")
    result = {"ok": True, "delivered": True, "text": "secret-content",
              "delivery_message_ids": ["123", "123", False, "secret-key"],
              "credentials": object()}
    delivery.observe(outcome, "reply_now", result)
    delivery.observe(outcome, "reply_now", result)
    assert result["delivery_message_ids"] == ["123", "123", False, "secret-key"]
    result["delivery_message_ids"].append("456")
    assert outcome["delivery_message_ids"] == ["123"]
    assert outcome["last_tools"] == ["reply_now"]
    serialized = json.dumps(outcome)
    assert "secret" not in serialized and "credentials" not in serialized
    delivery.observe(outcome, "qq_send", {"ok": False, "error": "secret-token", "exception": object()})
    assert "secret" not in json.dumps(outcome)


def test_finish_flags_do_not_coerce_strings_or_integers(delivery):
    """Malformed persisted metadata must never become a successful delivery."""
    outcome = delivery.fresh()
    outcome.update(delivery_attempted=True, delivery_confirmed=1)
    assert delivery.can_finish(outcome) is False
    outcome.update(delivery_confirmed=True, delivery_unknown="false")
    assert delivery.can_finish(outcome) is False
    assert delivery.can_finish({}) is False
