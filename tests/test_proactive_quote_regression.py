"""Offline regression for model-selected historical quotes in autonomous turns."""

import asyncio
import json

import pytest

from test_autonomy_flow import _poll_ticks
from test_production_integration import Event, Reply, _call, _provider
from plugin.message_batch import message_record

pytest_plugins = ("test_production_integration", "test_interim_reply_flow", "test_autonomy_flow", "test_task_flow")


@pytest.mark.parametrize("publication", ["reply_now", "final"])
@pytest.mark.parametrize("target_source, expected", [
    ("same_scope_record", ["8101"]),
    ("other_scope_record", []),
    ("forged_prose_only", []),
])
def test_autonomous_legacy_quote_uses_only_authentic_scope_records(
        native_autonomy, monkeypatch, publication, native_task_serializer,
        target_source, expected):
    """An observed historical native ID must survive either explicit publication path.

    The transport contract advertises a leading [quote:id] for existing native
    message IDs. Autonomous opportunities have no fabricated current messages;
    their real historical quote must not disappear merely because publication is
    represented internally as structured text. Only model HTTP and QQ transport
    are local fakes; the controller, native agent loop, decoder and sender are real.
    """
    native = native_autonomy
    native.controller.observe("42", "offline-native:GroupMessage:42")
    historical_scope = "77" if target_source == "other_scope_record" else "42"
    historical = Event("historical question", message_id="8101", group=historical_scope)
    # Shared public context may show another group's genuine record or a user-
    # supplied #ID string; neither authenticates a quote in this destination.
    metadata = (None if target_source == "forged_prose_only"
                else message_record(historical, historical.text))
    native.plugin._hist().append(historical_scope,
                                 "Earlier participant#8101: historical question",
                                 record=metadata)
    captures = []

    def send_native(action, parameters):
        """Capture native dispatch after the unchanged TaskEvent serialization boundary."""
        assert action == "send_group_msg" and parameters["group_id"] == 42
        captures.append(native_task_serializer[-1])
        return {"status": "ok", "retcode": 0, "data": {"message_id": 9101}}

    native.plugin._onebot_http = send_native
    quoted_text = "[quote:8101]A model-chosen answer to the historical question."
    if publication == "reply_now":
        # Omitted reply_to must not invent a current human message in autonomy.
        rounds = [("", [_call("reply_now", {"text": quoted_text, "finish": True})])]
    else:
        # delivery=new is required to publish an autonomous final completion.
        rounds = [(json.dumps({"reply_to": [], "text": quoted_text,
                              "delivery": "new"}), [])]
    requests = _provider(monkeypatch, rounds)

    _poll_ticks(native, [100600.0])

    envelope = json.loads(requests[0]["messages"][1]["content"])
    assert envelope["conversation"]["messages"] == []
    assert "Earlier participant#8101" in json.dumps(envelope, ensure_ascii=False)
    assert len(captures) == 1
    chain = captures[0]
    assert [str(part.id) for part in chain.chain if isinstance(part, Reply)] == expected
    assert "[quote:" not in "".join(getattr(part, "text", "") for part in chain.chain)


@pytest.mark.parametrize("publication", ["reply_now", "final"])
@pytest.mark.parametrize("explicit", [False, True])
def test_foreground_latest_selection_quotes_only_when_explicit(
        interim, monkeypatch, publication, explicit):
    """Latest-message attribution remains unquoted unless the model explicitly quotes it."""
    plugin, event = interim, Event("current question", message_id="1234")
    body = "A model-chosen answer."
    text = "[quote:1234]" + body if explicit else body
    if publication == "reply_now":
        # Omitted attribution still defaults to the real last current input.
        rounds = [("", [_call("reply_now", {"text": text, "finish": True})])]
    else:
        rounds = [(json.dumps({"reply_to": ["1234"], "text": text,
                              "delivery": "new"}), [])]
    requests = _provider(monkeypatch, rounds)

    asyncio.run(plugin.on_message(event))

    assert len(requests) == 1 and len(event.sent) == 1
    chain = event.sent[0]
    assert [str(part.id) for part in chain.chain if isinstance(part, Reply)] == (
        ["1234"] if explicit else [])
    assert "".join(getattr(part, "text", "") for part in chain.chain) == body


@pytest.mark.parametrize("publication", ["reply_now", "final"])
@pytest.mark.parametrize("private", [False, True])
def test_foreground_historical_quote_remains_in_its_native_scope(
        interim, monkeypatch, publication, private):
    """Foreground group/private turns retain actual old native IDs without quoting another scope."""
    plugin = interim
    group, sender = ("", "24680") if private else ("42", "777")
    scope = "private:24680" if private else "42"
    historical = Event("earlier same conversation", message_id="8101", group=group, sender_id=sender)
    plugin._hist().append(scope, "Earlier source#8101: earlier same conversation",
                          record=message_record(historical, historical.text))
    event = Event("new question", message_id="1234", group=group, sender_id=sender)
    text = "[quote:8101]An explicit answer using previous context."
    if publication == "reply_now":
        rounds = [("", [_call("reply_now", {"text": text, "finish": True})])]
    else:
        rounds = [(json.dumps({"reply_to": ["1234"], "text": text}), [])]
    _provider(monkeypatch, rounds)
    asyncio.run(plugin.on_message(event))
    assert len(event.sent) == 1
    chain = event.sent[0]
    assert [str(part.id) for part in chain.chain if isinstance(part, Reply)] == ["8101"]
    assert "[quote:" not in "".join(getattr(part, "text", "") for part in chain.chain)
