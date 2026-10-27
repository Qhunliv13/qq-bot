"""Native provider terminal-reply regressions; no language heuristics or live sends."""

import asyncio
import json

import pytest

from plugin.message_batch import message_record
import test_production_integration as integration
from test_production_integration import Event, _call, _provider

# Reuse the exact shared fixture while keeping static import checks meaningful.
production = integration.production


def test_delivered_explicit_finish_completes_siblings_without_second_provider(production, monkeypatch):
    """A successful terminal send ends only after every sibling is archived."""
    plugin, event = production(), Event("Finish this turn")
    plugin._turn_context["42"] = {"messages": [message_record(event, event.text)]}
    body = "Arbitrary terminal body; no completion keywords required."
    requests = _provider(monkeypatch, [("discard draft", [
        _call("reply_now", {"text": body, "finish": True}, "terminal"),
        _call("self_status", call_id="sibling"),
    ])])
    answer = asyncio.run(plugin._decide_tools_first(event, "42", event.text, False))
    assert answer == ""
    assert len(requests) == 1
    assert ["".join(getattr(part, "text", "") for part in chain.chain) for chain in event.sent] == [body]
    observed = plugin._tool_evidence().recent("42")
    assert [row["tool_name"] for row in observed] == ["reply_now", "self_status"]
    assert observed[0]["result"]["finish"] is True
    assert observed[0]["result"]["delivered"] is True
    assert observed[1]["result"]["ok"] is True


@pytest.mark.parametrize("explicit_finish", [False, None])
def test_default_or_false_finish_preserves_normal_multi_round(production, monkeypatch, explicit_finish):
    """Ordinary interim sends do not terminate even when their wording says done."""
    plugin, event = production(), Event()
    args = {"text": "All done; final answer; completed."}
    if explicit_finish is not None:
        args["finish"] = explicit_finish
    final = {"reply_to": [str(event.message_obj.message_id)],
             "text": "Normal later answer.", "delivery": "new"}
    plugin._turn_context["42"] = {"messages": [message_record(event, event.text)]}
    requests = _provider(monkeypatch, [("", [_call("reply_now", args)]),
                                      (json.dumps(final), [])])
    assert json.loads(asyncio.run(plugin._decide_tools_first(event, "42", event.text, False))) == final
    assert len(requests) == 2
    assert len(event.sent) == 1
    assert plugin._tool_evidence().recent("42")[0]["result"].get("finish") is not True


def test_failed_finish_delivery_cannot_end_generation(production, monkeypatch):
    """A requested terminal send is not completion until actual delivery succeeds."""
    plugin, event = production(), Event()
    event.fail_send = True
    requests = _provider(monkeypatch, [("", [_call("reply_now", {"text": "Failed body", "finish": True})]),
                                      ("Recovery answer.", [])])
    assert asyncio.run(plugin._decide_tools_first(event, "42", event.text, False)) == "Recovery answer."
    assert len(requests) == 2 and not event.sent
    receipt = plugin._tool_evidence().recent("42")[0]["result"]
    assert receipt["ok"] is False and receipt["delivered"] is False


def test_invalid_finish_type_never_sends_or_completes(production, monkeypatch):
    """JSON strings cannot be coerced into the trusted terminal boolean."""
    plugin, event = production(), Event()
    requests = _provider(monkeypatch, [("", [_call("reply_now", {"text": "Do not send", "finish": "true"})]),
                                      ("Still running.", [])])
    assert asyncio.run(plugin._decide_tools_first(event, "42", event.text, False)) == "Still running."
    assert len(requests) == 2 and not event.sent
