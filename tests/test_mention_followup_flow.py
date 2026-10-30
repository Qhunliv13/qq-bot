"""Native mention sends do not imply another public reply after tool completion."""

import asyncio

import pytest

from test_production_integration import Event, _call, _provider
from test_autonomy_flow import _poll_ticks
from plugin.message_batch import message_record

pytest_plugins = ("test_production_integration", "test_autonomy_flow", "test_task_flow")


def _run(plugin, event):
    """Keep actual tool loop, native dispatch, finalization and sender boundary."""
    plugin._turn_context["42"] = {"messages": [message_record(event, event.text)]}
    asyncio.run(plugin._handle_one(event, "42", event.text, False, True))


@pytest.mark.parametrize("final", [
    "已提醒。", "", "SILENT",
    {"reply_to": ["1234"], "text": "已提醒。"},
    {"reply_to": ["1234"], "text": "已提醒。", "delivery": "already_delivered"},
])
def test_confirmed_mention_does_not_force_followup(production, monkeypatch, final):
    """A confirmed mention counts as public output; silence/drafts are not another send."""
    plugin, event = production(), Event("Remind this member")
    calls = []

    def native(action, parameters):
        """Return one authoritative synthetic platform receipt, not a model assertion."""
        calls.append((action, parameters))
        return {"status": "ok", "retcode": 0, "data": {"message_id": 81234}}

    plugin._onebot_http = native
    requests = _provider(monkeypatch, [
        ("", [_call("qq_send", {"text": "", "mentions": ["12345"]})]),
        (final, []),
    ], auto_wrap=False)
    _run(plugin, event)
    assert len(calls) == 1
    assert calls[0][0] == "send_group_msg"
    assert calls[0][1]["message"][0] == {"type": "at", "data": {"qq": "12345"}}
    assert event.sent == []
    assert len(requests) == 2, "Do not solicit a separate final-reply repair after sending"
    deliveries = plugin._turn_context["42"]["_deliveries"]
    assert len(deliveries) == 1
    assert deliveries[0]["delivery"]["destination_scope"] == "42"
    assert deliveries[0]["delivery"]["message_ids"] == ["81234"]
    assert deliveries[0]["mentions"] == [{"id": "12345", "display_name": ""}]


def test_model_can_explicitly_choose_followup_after_mention(production, monkeypatch):
    """An explicit delivery=new remains an independent model-selected public message."""
    plugin, event = production(), Event()
    plugin._onebot_http = lambda *args: {"status": "ok", "retcode": 0, "data": {"message_id": 81234}}
    final = {"reply_to": ["1234"], "text": "另外补充一件事。", "delivery": "new"}
    requests = _provider(monkeypatch, [
        ("", [_call("qq_send", {"text": "提醒", "mentions": ["12345"]})]), (final, []),
    ], auto_wrap=False)
    _run(plugin, event)
    assert len(requests) == 2
    assert len(event.sent) == 1
    assert event.sent[0].chain[-1].text == final["text"]


@pytest.mark.parametrize("result", [
    {"status": "ok", "retcode": 0, "data": None},
    {"status": "failed", "retcode": 1},
])
def test_failed_or_unconfirmed_mention_does_not_fake_public_delivery(production, monkeypatch, result):
    """A missing native receipt cannot suppress an ordinary valid error reply."""
    plugin, event = production(), Event()
    plugin._onebot_http = lambda *args: result
    final = {"reply_to": ["1234"], "text": "未确认提醒成功。"}
    requests = _provider(monkeypatch, [
        ("", [_call("qq_send", {"text": "提醒", "mentions": ["12345"]})]), (final, []),
    ], auto_wrap=False)
    _run(plugin, event)
    assert len(requests) == 2
    assert len(event.sent) == 1
    assert event.sent[0].chain[-1].text == final["text"]
    assert all(row.get("delivery", {}).get("message_ids") != ["81234"]
               for row in plugin._turn_context["42"]["_deliveries"])


def test_other_group_mention_does_not_suppress_current_group_reply(production, monkeypatch):
    """A different destination's receipt is real evidence, not current-scope publication."""
    plugin, event = production(), Event()
    plugin._onebot_http = lambda *args: {"status": "ok", "retcode": 0, "data": {"message_id": 81234}}
    final = {"reply_to": ["1234"], "text": "这里仍可正常回答。"}
    _provider(monkeypatch, [
        ("", [_call("qq_send", {"group_id": 43, "text": "提醒", "mentions": ["12345"]})]),
        (final, []),
    ], auto_wrap=False)
    _run(plugin, event)
    assert len(event.sent) == 1
    assert event.sent[0].chain[-1].text == final["text"]


@pytest.mark.parametrize("final,extra", [
    ("已提醒。", False), ("SILENT", False),
    ({"reply_to": [], "text": "已提醒。"}, False),
    ({"reply_to": [], "text": "再补充一句。", "delivery": "new"}, True),
])
def test_autonomous_mention_keeps_followup_model_selected(
        native_autonomy, native_task_serializer, monkeypatch, final, extra):
    """Real autonomous polling may stop after @ or explicitly publish another message."""
    native = native_autonomy
    native.controller.observe("42", "offline-native:GroupMessage:42")
    sends = []

    def native_send(action, parameters):
        """Capture independent native sends and return genuine-shaped receipt envelopes."""
        sends.append((action, parameters))
        return {"status": "ok", "retcode": 0, "data": {"message_id": 81234 + len(sends)}}

    native.plugin._onebot_http = native_send
    requests = _provider(monkeypatch, [
        ("", [_call("qq_send", {"text": "", "mentions": ["12345"]})]), (final, []),
    ], auto_wrap=False)
    _poll_ticks(native, [100600.0])
    assert len(requests) == 2
    assert len(sends) == (2 if extra else 1)
    assert sends[0][1]["message"][0] == {"type": "at", "data": {"qq": "12345"}}
    assert len(native_task_serializer) == (1 if extra else 0)
    assert native.plugin._turn_context == {}


@pytest.mark.parametrize("target", ["042", 42.0, "４２"])
def test_numeric_target_alias_uses_actual_destination_for_publication(production, monkeypatch, target):
    """A numeric alias cannot evade the actual target's policy or receipt tracking."""
    plugin, event = production(), Event()
    plugin._onebot_http = lambda *args: {"status": "ok", "retcode": 0, "data": {"message_id": 81234}}
    _provider(monkeypatch, [
        ("", [_call("qq_send", {"group_id": target, "text": "", "mentions": ["12345"]})]),
        ({"reply_to": ["1234"], "text": "不应自动公开。"}, []),
    ], auto_wrap=False)
    _run(plugin, event)
    assert event.sent == []
    assert plugin._turn_context["42"]["_deliveries"][0]["delivery"]["destination_scope"] == "42"


def test_mention_does_not_end_tools_or_deduplicate_explicit_sends(production, monkeypatch):
    """The model can keep working and choose a second @; receipt tracking is not a stop rule."""
    plugin, event = production(), Event()
    sends = []

    def native_send(action, parameters):
        """Keep both intentional native actions independently observable."""
        sends.append((action, parameters))
        return {"status": "ok", "retcode": 0, "data": {"message_id": 81234 + len(sends)}}

    plugin._onebot_http = native_send
    requests = _provider(monkeypatch, [
        ("", [_call("qq_send", {"text": "", "mentions": ["12345"]}, "first")]),
        ("", [_call("qq_send", {"text": "", "mentions": ["12345"]}, "second")]),
        ("SILENT", []),
    ], auto_wrap=False)
    _run(plugin, event)
    assert len(requests) == 3 and len(sends) == 2
    assert event.sent == []
    assert [row["delivery"]["message_ids"] for row in plugin._turn_context["42"]["_deliveries"]] == [
        ["81235"], ["81236"]]


@pytest.mark.parametrize("kind", ["private", "temp"])
def test_private_tool_send_is_not_recorded_as_group_delivery(production, monkeypatch, kind):
    """Temporary private routing uses a group ID but its public record stays private."""
    plugin, event = production(), Event()
    plugin._onebot_http = lambda *args: {"status": "ok", "retcode": 0, "data": {"message_id": 81234}}
    final = {"reply_to": ["1234"], "text": "本群仍然正常回答。"}
    _provider(monkeypatch, [
        ("", [_call("qq_send", {"kind": kind, "user_id": 12345, "text": "私聊提醒"})]),
        (final, []),
    ], auto_wrap=False)
    _run(plugin, event)
    assert len(event.sent) == 1
    assert event.sent[0].chain[-1].text == final["text"]
    private = plugin._hist().load_records("private:12345")
    assert private[-1]["record"]["delivery"]["message_ids"] == ["81234"]
    assert private[-1]["record"]["delivery"]["destination_scope"] == "private:12345"
    assert not any(row.get("record", {}).get("id") == "81234"
                   for row in plugin._hist().load_records("42"))


def test_mention_receipt_does_not_silence_next_turn(production, monkeypatch):
    """A later incoming message retains normal final reply behavior after @ was sent."""
    plugin, event = production(), Event()
    plugin._onebot_http = lambda *args: {"status": "ok", "retcode": 0, "data": {"message_id": 81234}}
    later = Event("A later question", message_id="1235")
    _provider(monkeypatch, [
        ("", [_call("qq_send", {"text": "", "mentions": ["12345"]})]), ("SILENT", []),
        ({"reply_to": ["1235"], "text": "正常回答下一条消息。"}, []),
    ], auto_wrap=False)
    _run(plugin, event)
    _run(plugin, later)
    assert event.sent == []
    assert len(later.sent) == 1
    assert later.sent[0].chain[-1].text == "正常回答下一条消息。"
