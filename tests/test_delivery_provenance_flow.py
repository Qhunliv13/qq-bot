"""Verify truthful native delivery provenance through production send and dispatch."""

import asyncio
import json

import pytest

from test_interim_reply_flow import _texts
from test_production_integration import Event, _call, _provider
from plugin.autonomous_turn import AutonomyEvent
from plugin.task_event import TaskEvent

pytest_plugins = ("test_production_integration", "test_interim_reply_flow")


class NativeEvent(Event):
    """Expose native positive message IDs without a network transport."""

    async def send(self, chain):
        """Return distinct native receipts for each actual visible send."""
        self.sent.append(chain)
        return [{"message_id": 81000 + len(self.sent)}]


def test_reply_now_exposes_only_current_confirmed_receipt(interim):
    """The real dispatcher returns a frozen receipt, not all-batch attribution."""
    plugin, event = interim, NativeEvent()
    plugin._turn_context["42"] = {"messages": [{"id": "1234"}, {"id": "1235"}]}
    result = asyncio.run(plugin._dispatch_tool(
        "reply_now", {"text": "First public statement.", "reply_to": ["1234"]}, "42", event))
    assert result["delivered"] is True
    delivery = result["delivery"]
    assert delivery["status"] == "native_confirmed"
    assert delivery["message_ids"] == ["81001"]
    assert delivery["reply_to"] == ["1234"]
    assert delivery["input_message_ids"] == ["1234", "1235"]
    assert delivery["destination_scope"] == "42"
    record = plugin._turn_context["42"]["_deliveries"][-1]
    assert record["id"] == "81001" and record["body"] == result["text"]
    assert record["sender"] == {"id": "90001", "display_name": ""}
    assert record["kind"] == "assistant_delivery"
    assert record["source"]["kind"] == "foreground"
    assert record["quote"] == {"id": "1234"}
    record["delivery"]["message_ids"].append("changed-after-result")
    assert delivery["message_ids"] == ["81001"]
    assert plugin._last_sent == {"42": [81001]}
    stored = plugin._hist().load_records("42")[-1]
    assert stored["record"]["delivery"]["message_ids"] == ["81001"]
    assert isinstance(stored["ts"], int)


@pytest.mark.parametrize("receipt", [None, [], [{"message_id": 0}], [{"message_id": False}]])
def test_observed_return_never_claims_native_confirmation(interim, receipt):
    """Legacy return semantics stay intact without inventing a platform receipt."""
    plugin, event = interim, NativeEvent()
    plugin._turn_context["42"] = {"messages": [{"id": "1234"}]}

    async def unconfirmed(chain):
        """Observe exactly one send without native delivery evidence."""
        event.sent.append(chain)
        return receipt

    event.send = unconfirmed
    result = asyncio.run(plugin._dispatch_tool("reply_now", {"text": "Observed only."}, "42", event))
    assert result == {"ok": True, "delivered": True, "text": "Observed only.", "quote_id": None}
    record = plugin._turn_context["42"]["_deliveries"][-1]
    assert record["delivery"]["status"] == "observed_send_return"
    assert record["delivery"]["message_ids"] == []
    assert record["id"] == "" and record["quote"] is None
    assert record["delivery"]["reply_to"] == []


def test_send_failure_writes_no_history_or_receipt(interim):
    """A transport exception cannot manufacture a successful source record."""
    plugin, event = interim, NativeEvent()
    plugin._turn_context["42"] = {"messages": [{"id": "1234"}]}

    async def failed(chain):
        """Fail offline before any native send is observed."""
        raise RuntimeError("offline failure")

    event.send = failed
    result = asyncio.run(plugin._dispatch_tool("reply_now", {"text": "Never sent."}, "42", event))
    assert result["delivered"] is False and "delivery" not in result
    assert "_deliveries" not in plugin._turn_context["42"]
    assert plugin._hist().load("42") == [] and plugin._last_sent == {}


def test_multiple_same_kind_sends_keep_distinct_native_records(interim):
    """No content comparison suppresses or merges separately delivered utterances."""
    plugin, event = interim, NativeEvent()
    plugin._turn_context["42"] = {"messages": [{"id": "1234"}]}
    for body in ("The reading is seventeen.", "The measurement equals seventeen."):
        result = asyncio.run(plugin._dispatch_tool("reply_now", {"text": body}, "42", event))
        assert result.get("finish", False) is False
    records = plugin._turn_context["42"]["_deliveries"]
    assert [record["delivery"]["message_ids"] for record in records] == [["81001"], ["81002"]]
    assert [record["body"] for record in records] == _texts(event.sent)
    assert all(record["delivery"]["reply_to"] == [] for record in records)
    assert len(plugin._hist().load_records("42")) == 2


@pytest.mark.parametrize("kind", ["task", "autonomous"])
def test_background_delivery_uses_typed_source_and_unknown_self(interim, kind):
    """Background labels never become a human, owner, or invented bot identity."""
    plugin = interim
    origin = "offline-native:GroupMessage:42"
    event = (TaskEvent(plugin, {"scope": "42", "origin": origin}) if kind == "task"
             else AutonomyEvent(plugin, "42", origin, []))
    plugin._turn_context["42"] = {"messages": []}
    result = asyncio.run(plugin._dispatch_tool("reply_now", {"text": "Background evidence."}, "42", event))
    assert result["delivery"]["source"] == {"kind": kind}
    record = plugin._turn_context["42"]["_deliveries"][-1]
    assert record["sender"] == {"id": "", "display_name": ""}
    assert record["delivery"]["reply_to"] == record["delivery"]["input_message_ids"] == []


def test_provider_sees_native_receipt_before_arbitrary_final(interim, monkeypatch):
    """Production native transcript exposes real receipt metadata, not paraphrase guesses."""
    plugin, event = interim, NativeEvent()
    body = "The measured value is seventeen."
    final = "Seventeen is the resulting measurement."
    requests = _provider(monkeypatch, [
        ("private", [_call("reply_now", {"text": body, "finish": False})]),
        (json.dumps({"reply_to": ["1234"], "text": final, "delivery": "new"}), []),
    ])
    asyncio.run(plugin.on_message(event))
    receipt = json.loads(requests[1]["messages"][-1]["content"])["result"]["delivery"]
    assert receipt["message_ids"] == ["81001"] and receipt["status"] == "native_confirmed"
    assert receipt["source"] == {"kind": "foreground"}
    assert _texts(event.sent) == [body, final]
    records = plugin._hist().load_records("42")
    outgoing = [item["record"] for item in records if item.get("record", {}).get("kind") == "assistant_delivery"]
    assert [record["body"] for record in outgoing] == [body, final]
    assert [record["id"] for record in outgoing] == ["81001", "81002"]


@pytest.mark.parametrize("body", ["The measured value is seventeen.", "A completely unrelated update."])
def test_explicit_new_final_is_content_independent(interim, monkeypatch, body):
    """Explicit publication works for identical and unrelated text without comparison."""
    plugin, event = interim, NativeEvent()
    first = "The measured value is seventeen."
    _provider(monkeypatch, [
        ("private", [_call("reply_now", {"text": first, "finish": False})]),
        (json.dumps({"reply_to": ["1234"], "text": body, "delivery": "new"}), []),
    ])
    asyncio.run(plugin.on_message(event))
    assert _texts(event.sent) == [first, body]


def test_multi_receipt_keeps_native_ids_and_actual_mentions(interim):
    """One send with several platform IDs is not represented by a fabricated ID."""
    plugin, event = interim, NativeEvent()
    plugin._turn_context["42"] = {"messages": [{"id": 1234}, {"id": "1235"}]}

    async def multi_receipt(chain):
        """Return several authoritative transport IDs from one actual send."""
        event.sent.append(chain)
        return [{"message_id": 82001}, {"message_id": "82002"}]

    event.send = multi_receipt
    asyncio.run(plugin._send(event, "42", "[at:12345] Actual mention.", decoded=True))
    record = plugin._turn_context["42"]["_deliveries"][-1]
    assert record["id"] == "" and record["message_ids"] == ["82001", "82002"]
    assert record["delivery"]["message_ids"] == record["message_ids"]
    assert record["delivery"]["input_message_ids"] == [1234, "1235"]
    assert record["delivery"]["reply_to"] == []
    assert record["mentions"] == [{"id": "12345", "display_name": ""}]
    assert plugin._last_sent == {"42": [82001, "82002"]}
