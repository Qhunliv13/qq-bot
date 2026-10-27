"""Offline checks for model-chosen public replies through the shared decoder."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from plugin.interim_reply import already_delivered, deliver, schema
from plugin.reply_selection import decode_reply


RECORDS = [{"id": "101"}, {"id": "102"}]


def run(args, records=RECORDS, send=None):
    """Run one isolated action without framework dependencies or real network."""
    callback = send if send is not None else AsyncMock(return_value="actual body")
    return asyncio.run(deliver(args, records, callback)), callback


def test_schema_has_one_optional_free_form_action():
    """The model chooses wording and cadence; no phase/purpose enum is required."""
    tool, = schema()
    assert tool["function"]["name"] == "reply_now"
    parameters = tool["function"]["parameters"]
    assert parameters["required"] == ["text"]
    assert set(parameters["properties"]) == {"text", "reply_to", "finish"}
    assert parameters["properties"]["finish"] == {"type": "boolean", "default": False}
    assert parameters["properties"]["reply_to"]["items"] == {"type": "string"}
    assert parameters["additionalProperties"] is False
    assert "无需每轮发送" in tool["function"]["description"]


def test_default_target_uses_last_actual_record_and_returns_transport_body():
    """The history ledger needs cleaned output, not the model's submitted text."""
    result, send = run({"text": "任意模型表达"})
    send.assert_awaited_once_with("任意模型表达", None)
    assert result == {"ok": True, "delivered": True, "text": "actual body", "quote_id": None}


@pytest.mark.parametrize("finish", [None, 0, 1, "true", [], {}])
def test_finish_requires_actual_boolean(finish):
    """Completion must not be authorized by JSON truthiness or coercion."""
    result, send = run({"text": "回答", "finish": finish})
    assert result["error"] == "invalid_arguments"
    send.assert_not_awaited()


def test_confirmed_delivery_carries_explicit_completion_only():
    """The opt-in flag follows the actual transport body, not model claims."""
    result, _ = run({"text": "回答", "finish": True})
    assert result == {"ok": True, "delivered": True, "text": "actual body",
                      "quote_id": None, "finish": True}
    result, _ = run({"text": "回答", "finish": False})
    assert "finish" not in result


@pytest.mark.parametrize("text,actual", [("SILENT", "body"), ("", "body"),
                                          ("回答", None), ("回答", " ")])
def test_finish_is_not_confirmed_by_silence_or_transport_suppression(text, actual):
    """A requested terminal reply without real delivery cannot stop generation."""
    result, _ = run({"text": text, "finish": True}, send=AsyncMock(return_value=actual))
    assert result["delivered"] is False
    assert "finish" not in result


def test_finish_is_not_confirmed_after_sender_failure():
    """Uncertain transport outcomes remain failures even with a finish request."""
    result, send = run({"text": "回答", "finish": True},
                       send=AsyncMock(side_effect=RuntimeError("private")))
    assert result["delivered"] is False
    assert "finish" not in result
    assert send.await_count == 1


def test_earlier_current_target_supplies_validated_quote():
    """Existing attribution rules select an earlier batch message for quotation."""
    result, send = run({"text": "更新", "reply_to": ["101", "102"]})
    send.assert_awaited_once_with("更新", "101")
    assert result["quote_id"] == "101"


def test_default_integer_record_id_is_not_fabricated():
    """Existing decoder normalizes an actual integer message ID once."""
    result, send = run({"text": "答复"}, [{"id": 101}])
    assert result["delivered"] is True
    send.assert_awaited_once_with("答复", None)


@pytest.mark.parametrize("args", [None, [], {}, {"text": None}, {"text": 1},
    {"text": False}, {"text": []}, {"text": {}}, {"text": "a", "phase": "start"}])
def test_invalid_arguments_never_send(args):
    """The action does not coerce text or quietly accept invented schema fields."""
    result, send = run(args)
    assert result == {"ok": False, "delivered": False, "error": "invalid_arguments"}
    send.assert_not_awaited()


@pytest.mark.parametrize("targets", [None, "101", {}, [101], [True], [None],
    [{}], [[]], [], ["999"], ["101", "999"], [""]])
def test_invalid_or_out_of_batch_targets_never_send(targets):
    """Whole-list validation rejects invalid attribution rather than repairing it."""
    result, send = run({"text": "更新", "reply_to": targets})
    assert result == {"ok": False, "delivered": False, "error": "invalid_reply_targets"}
    send.assert_not_awaited()


@pytest.mark.parametrize("text", ["", "  ", "SILENT", "silent!", '"SILENT"', "（SILENT）"])
def test_valid_silence_is_a_successful_noop(text):
    """Control text creates neither transport effects nor a delivery ledger entry."""
    result, send = run({"text": text})
    assert result == {"ok": True, "delivered": False}
    send.assert_not_awaited()


def test_silence_does_not_bypass_target_validation():
    """The shared decoder validates attribution before honoring control tokens."""
    result, send = run({"text": "SILENT", "reply_to": ["999"]})
    assert result["error"] == "invalid_reply_targets"
    send.assert_not_awaited()


@pytest.mark.parametrize("args", [{"text": "无需引用"}, {"text": "无需引用", "reply_to": []}])
def test_no_batch_may_send_without_a_fabricated_target(args):
    """Persisted tasks can use an existing destination with no current message."""
    result, send = run(args, [])
    assert result["delivered"] is True
    send.assert_awaited_once_with("无需引用", None)


def test_no_batch_rejects_explicit_message_target():
    """No history record can be promoted into a current-batch quote."""
    result, send = run({"text": "回答", "reply_to": ["101"]}, [])
    assert result["error"] == "invalid_reply_targets"
    send.assert_not_awaited()


@pytest.mark.parametrize("records", [[{}], [{"id": True}], ["not a record"]])
def test_missing_actual_last_id_is_not_guessed(records):
    """Malformed current records cannot authorize a new message ID."""
    result, send = run({"text": "回答"}, records)
    assert result["error"] == "invalid_reply_targets"
    send.assert_not_awaited()


def test_nested_json_is_literal_and_decoder_runs_once():
    """A JSON-shaped public answer is not recursively reinterpreted as routing."""
    text = json.dumps({"reply_to": ["999"], "text": "SILENT"})
    with patch("plugin.interim_reply.decode_reply", wraps=decode_reply) as decoder:
        result, send = run({"text": text})
    assert decoder.call_count == 1
    send.assert_awaited_once_with(text, None)
    assert result["delivered"] is True


def test_embedded_quote_cannot_override_selected_batch_target():
    """Only the shared decoder's validated quote reaches the transport callback."""
    result, send = run({"text": "[quote:999]内容", "reply_to": ["101"]})
    send.assert_awaited_once_with("内容", "101")
    assert result["quote_id"] == "101"


@pytest.mark.parametrize("actual", [None, "", "  ", False, 1])
def test_unconfirmed_delivery_fails_without_retry(actual):
    """Suppression/failure is not recorded as confirmed delivery or retried."""
    result, send = run({"text": "更新"}, send=AsyncMock(return_value=actual))
    assert result == {"ok": False, "delivered": False, "error": "delivery_not_confirmed"}
    assert send.await_count == 1


def test_sender_exception_never_leaks_details_or_blindly_retries():
    """A possibly-sent transport error cannot justify a duplicate automatic send."""
    result, send = run({"text": "更新"}, send=AsyncMock(side_effect=RuntimeError("secret-token")))
    assert result == {"ok": False, "delivered": False, "error": "send_exception",
                      "exception_type": "RuntimeError"}
    assert "secret-token" not in json.dumps(result)
    assert send.await_count == 1


def test_two_model_selected_replies_then_distinct_final_are_unrestricted():
    """Dynamic wording and repeated calls do not impose templates or cadence."""
    async def scenario():
        """Retain one parent-local ledger while the same tool loop continues."""
        sender = AsyncMock(side_effect=["任意确认文字", "独立进展文字", "独立进展文字"])
        ledger = []
        for text in ("任意确认文字", "独立进展文字", "独立进展文字"):
            result = await deliver({"text": text}, RECORDS, sender)
            assert result["delivered"]
            ledger.append(result["text"])
        assert not already_delivered("最终的新结果", ledger)
        assert already_delivered(" 独立进展文字\n", ledger)
        assert sender.await_count == 3
    asyncio.run(scenario())


@pytest.mark.parametrize("final,bodies,expected", [
    (" 完成\n", ["完成"], True), ("完成内容", ["完成"], False),
    ("完成", ["完成内容"], False), ("完成!", ["完成！"], False),
    ("完成", [], False), ("", [""], False), ("  ", ["  "], False),
    (None, ["完成"], False), ("完成", [None], False),
])
def test_final_dedup_is_only_exact_trimmed_confirmed_body(final, bodies, expected):
    """No substring, semantic, or cross-turn heuristic suppresses a final reply."""
    assert already_delivered(final, bodies) is expected
