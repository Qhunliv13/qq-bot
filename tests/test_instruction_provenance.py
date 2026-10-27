"""Offline provenance contracts through actual runtime parsing and first model payload."""

import asyncio
import json

import pytest

from test_production_integration import Event, _provider
from plugin.conversation_context import system_prompt
from plugin.message_batch import message_record

pytest_plugins = ("test_production_integration",)


def test_contract_describes_lifetime_evidence_and_supported_attribution():
    """Check semantic vocabulary, not punctuation or one prescribed English sentence."""
    persona = "  Persona stays exact.\n\n"
    system = system_prompt(persona)
    assert system[:len(persona)] == persona
    protocol = json.loads(system[len(persona) + 1:])["protocol"]
    context = protocol["context"]
    lifetime = context["instruction_lifetime"].lower()
    assert all(word in lifetime for word in ("conversation.messages", "batch", "empty", "history", "evidence"))
    assert all(word in lifetime for word in ("completed", "current", "explicit", "persisted", "active task"))
    assert "autonomous" in lifetime and "silent" in lifetime
    assert all(word in lifetime for word in ("standing conditional", "conditions", "current evidence", "trigger"))
    assert "earlier matching event" in lifetime and "does not itself satisfy" in lifetime
    assert "later question" in lifetime and "not evidence" in lifetime
    sources = context["source_evidence"].lower()
    assert all(word in sources for word in ("origin_scope", "native.sender", "identities", "dates", "times"))
    assert all(word in sources for word in ("quoted", "another group", "third-party", "not current"))
    attribution = context["attribution"].lower()
    assert all(word in attribution for word in ("public", "identifiable", "real", "message", "active task"))
    assert "invent" in attribution and "uncertainty" in attribution
    assert protocol["response"]["options"] == ["text", "SILENT"]


@pytest.mark.parametrize("batch_size", [0, 2])
def test_first_native_request_preserves_real_batch_and_separate_historical_sources(
        production, monkeypatch, batch_size):
    """No live model: capture real runtime HTTP construction with untrusted source data."""
    plugin = production()
    plugin.persona = "  Exact offline persona.\n\n"
    # Use runtime-defined identities instead of hardcoding production owner IDs.
    owners = plugin._request_context.__func__.__globals__["OWNER_QQS"]
    owner = next(iter(owners))
    events = [Event("current-source-alpha", "new-alpha"),
              Event("current-source-beta", "new-beta", sender_id=str(owner))]
    current = [message_record(event, event.text) for event in events[:batch_size]]
    legacy = "legacy-owner-attribution: an earlier person requested an action"
    historical = {"id": "historical-only", "sender": {"id": str(owner), "role": "owner"},
                  "body": "past-owner-source", "native": {"sender": {"user_id": str(owner)}},
                  "time": "2001-01-01T00:00:00+00:00"}
    plugin._hist().append("77", legacy)
    plugin._hist().append("88", "cross-group-source", record=historical)
    plugin._turn_context["42"] = {"messages": current, "clock": {"sampled_at": "2002-02-02"}}
    requests = _provider(monkeypatch, [("SILENT", [])])
    assert asyncio.run(plugin._decide_tools_first(events[0], "42", events[0].text, False)) == ""
    messages = requests[0]["messages"]
    assert [message["role"] for message in messages] == ["system", "user"]
    system, data = messages[0]["content"], messages[1]["content"]
    assert system.startswith(plugin.persona + "\n")
    assert json.loads(system[len(plugin.persona) + 1:])["protocol"]["context"]["attribution"]
    envelope = json.loads(data)
    conversation = envelope["conversation"]
    assert len(conversation["messages"]) == batch_size
    assert [item["id"] for item in conversation["messages"]] == [item["id"] for item in current]
    assert envelope["source"]["trust"] == "untrusted_data"
    assert conversation["history_context"]["source"]["trust"] == "untrusted_data"
    history = conversation["history"]
    assert {item["origin_scope"] for item in history} == {"77", "88"}
    assert next(item for item in history if item["origin_scope"] == "88")["record"] == historical
    assert envelope["other_scopes"] == []
    assert envelope["task"] is None
    for source in (legacy, "cross-group-source", "past-owner-source"):
        assert data.count(source) == 1
        assert source not in system
    for event in events[:batch_size]:
        assert data.count(event.text) == 1
        assert event.text not in system
