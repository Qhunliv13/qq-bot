"""Optional model-selected delivery disposition, never semantic deduplication."""

import json

import pytest

from plugin.conversation_context import system_prompt
from plugin.reply_selection import decode_reply


@pytest.mark.parametrize("targets", [[], ["101"], [101, "102", 101]])
def test_already_delivered_draft_has_no_public_body_or_quote(targets):
    """An explicit internal draft cannot restate a previous public answer."""
    raw = json.dumps({"reply_to": targets, "text": "[quote:101]改写后的同一回答",
                      "delivery": "already_delivered"})
    result = decode_reply(raw, [{"id": "101"}, {"id": "102"}])
    assert result["text"] == ""
    assert result["quote_id"] is None
    assert result["error"] is None
    assert result["delivery"] == "already_delivered"
    assert result["reply_to"] == list(dict.fromkeys(map(str, targets)))
    assert "success" not in result
    assert "receipt" not in result


@pytest.mark.parametrize("targets", [["999"], ["101", "999"], [True], None, "101"])
def test_already_delivered_does_not_bypass_target_validation(targets):
    """Internal disposition cannot authorize invented attribution metadata."""
    raw = json.dumps({"reply_to": targets, "text": "draft", "delivery": "already_delivered"})
    result = decode_reply(raw, [{"id": "101"}])
    assert result["error"] == "invalid_reply_targets"
    assert result["text"] == ""
    assert result["reply_to"] == []
    assert result["quote_id"] is None


@pytest.mark.parametrize("delivery", [None, True, False, 0, [], {}, "NEW", "", "replace_previous"])
def test_invalid_delivery_fails_closed(delivery):
    """Only the explicitly specified string enum may change public disposition."""
    raw = json.dumps({"reply_to": ["101"], "text": "draft", "delivery": delivery})
    result = decode_reply(raw, [{"id": "101"}])
    assert result["structured"] is True
    assert result["error"] == "invalid_reply_delivery"
    assert result["text"] == ""
    assert result["quote_id"] is None


@pytest.mark.parametrize("text", [None, True, 0, [], {}])
def test_internal_draft_still_requires_string_text(text):
    """An explicit no-send choice does not relax the existing text schema."""
    raw = json.dumps({"reply_to": [], "text": text, "delivery": "already_delivered"})
    result = decode_reply(raw, [{"id": "101"}])
    assert result["error"] == "invalid_reply_targets"
    assert result["text"] == ""


@pytest.mark.parametrize("delivery", [None, "new"])
def test_ordinary_and_explicit_new_replies_preserve_multi_reply_compatibility(delivery):
    """No state, phrase matching, or single-reply restriction is introduced."""
    for text in ("才没拟人喵，猫本来就长这样", "才没拟人喵，猫本来就这样", "下一条新进展"):
        payload = {"reply_to": ["101", "102"], "text": text}
        if delivery is not None:
            payload["delivery"] = delivery
        result = decode_reply(json.dumps(payload), [{"id": "101"}, {"id": "102"}])
        assert result["text"] == text
        assert result["reply_to"] == ["101", "102"]
        assert result["quote_id"] == "101"
        assert result["error"] is None
        assert ("delivery" in result) == (delivery is not None)


@pytest.mark.parametrize("records", [[], None])
def test_unattributed_task_requires_existing_explicit_protocol_opt_in(records):
    """A default no-batch JSON answer remains literal despite the added field."""
    raw = json.dumps({"reply_to": [], "text": "draft", "delivery": "already_delivered"})
    legacy = decode_reply(raw, records)
    assert legacy["text"] == raw
    assert legacy["structured"] is False
    internal = decode_reply(raw, records, allow_unattributed=True)
    assert internal["text"] == ""
    assert internal["error"] is None
    assert internal["quote_id"] is None


@pytest.mark.parametrize("wrapper", ["`{}`", "```json\n{}\n```", "Example: {}"])
def test_quoted_and_fenced_delivery_examples_remain_literal(wrapper):
    """Examples are not interpreted recursively as control envelopes."""
    raw = wrapper.format(json.dumps({"reply_to": [], "text": "draft", "delivery": "already_delivered"}))
    result = decode_reply(raw, [{"id": "101"}])
    assert result["text"] == raw
    assert result["structured"] is False


def test_delivery_contract_keeps_persona_and_observed_receipt_semantics():
    """The neutral transport contract offers dispositions without inventing proof."""
    persona = "原有人设\n"
    prompt = system_prompt(persona)
    assert prompt.startswith(persona + "\n")
    contract = json.loads(prompt[len(persona) + 1:])["protocol"]["response"]
    assert "exactly new or already_delivered" in contract["selection"]
    assert "Omitting it preserves ordinary reply behavior" in contract["selection"]
    assert "not evidence that delivery succeeded" in contract["selection"]
    assert "observed public receipt" in contract["intermediate_delivery"]
    assert "Further substantive public messages remain available" in contract["intermediate_delivery"]
