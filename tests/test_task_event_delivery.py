"""Offline contracts for native persisted-task delivery and real receipt checks.

Only the imported framework serializer and existing transport are replaced. No
framework startup, subprocess, model invocation or live OneBot send is involved.
"""

import asyncio
import sys
from types import SimpleNamespace

import pytest

from plugin.task_event import TaskEvent


@pytest.fixture
def native_delivery(monkeypatch):
    """Expose the installed adapter API without importing optional live dependencies."""
    calls = {"serialized": [], "native": [], "context": []}
    state = {"segments": [{"type": "text", "data": {"text": "literal [CQ:at,qq=9]"}}],
             "response": {"status": "ok", "retcode": 0, "data": {"message_id": 7654321}}}

    async def parse(chain):
        """Observe native serialization once while leaving media logic in the framework."""
        calls["serialized"].append(chain)
        return state["segments"]

    async def context_send(*args):
        """Fail if task delivery trusts the framework's non-receipt routing boolean."""
        calls["context"].append(args)
        raise AssertionError("context_send_is_not_a_platform_receipt")

    def transport(action, parameters):
        """Record one scope-bound native action without opening a network connection."""
        calls["native"].append((action, parameters))
        response = state["response"]
        if isinstance(response, Exception):
            raise response
        return response

    module = "astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event"
    monkeypatch.setitem(sys.modules, module, SimpleNamespace(
        AiocqhttpMessageEvent=SimpleNamespace(_parse_onebot_json=parse)))
    plugin = SimpleNamespace(context=SimpleNamespace(send_message=context_send), _onebot_http=transport)
    return plugin, state, calls


@pytest.mark.parametrize("scope,origin,action,target", [
    ("42", "napcat:GroupMessage:42", "send_group_msg", {"group_id": 42}),
    ("private:10001", "napcat:FriendMessage:10001", "send_private_msg", {"user_id": 10001}),
])
def test_task_native_delivery_requires_saved_origin_and_returns_platform_id(native_delivery, scope, origin, action, target):
    """Group/private routing and the actual message ID survive the normal send boundary."""
    plugin, state, calls = native_delivery
    chain = SimpleNamespace(chain=[object()])
    event = TaskEvent(plugin, {"scope": scope, "origin": origin})
    sent = asyncio.run(event.send(chain))
    assert sent == [{"ok": True, "delivered": True, "message_id": "7654321", "action": action}]
    assert event.last_delivery_receipt == sent[0]
    assert calls["serialized"] == [chain]
    assert calls["native"] == [(action, dict(target, message=state["segments"]))]
    assert calls["context"] == []
    assert event.get_sender_id() == "background-task" and event.get_self_id() == ""


def test_task_uses_framework_native_media_and_mentions_without_reencoding(native_delivery):
    """The exact adapter-produced array is forwarded; CQ-looking text stays literal."""
    plugin, state, calls = native_delivery
    state["segments"] = [
        {"type": "text", "data": {"text": "literal [CQ:at,qq=9]"}},
        {"type": "at", "data": {"qq": "10001"}},
        {"type": "image", "data": {"file": "base64://aW1hZ2U="}},
        {"type": "file", "data": {"file": "file:///offline/report.txt", "name": "report.txt"}},
        {"type": "record", "data": {"file": "base64://YXVkaW8="}},
        {"type": "reply", "data": {"id": "1234"}},
    ]
    event = TaskEvent(plugin, {"scope": "42", "origin": "napcat:GroupMessage:42"})
    asyncio.run(event.send(SimpleNamespace(chain=[object()])))
    assert calls["native"][0][1]["message"] is state["segments"]
    assert len(calls["native"]) == 1 and calls["context"] == []


@pytest.mark.parametrize("response", [
    False, True, None, {}, {"ok": True, "data": None},
    {"status": "ok", "retcode": 0, "data": None},
    {"status": "ok", "retcode": 0, "data": {}},
    {"status": "ok", "retcode": 0, "data": {"message_id": 0}},
    {"status": "ok", "retcode": 0, "data": {"message_id": True}},
    {"status": "ok", "retcode": 0, "data": {"message_id": "invented-id"}},
    {"status": "failed", "retcode": 100, "data": {"message_id": 123}},
    {"status": "async", "retcode": 1, "data": {"message_id": 123}},
])
def test_task_rejects_boolean_null_and_failed_receipts_without_retry(native_delivery, response):
    """No exception-free acknowledgement becomes success unless OneBot confirms an ID."""
    plugin, state, calls = native_delivery
    state["response"] = response
    event = TaskEvent(plugin, {"scope": "42", "origin": "napcat:GroupMessage:42"})
    with pytest.raises(RuntimeError, match="^task_delivery_unconfirmed$"):
        asyncio.run(event.send(SimpleNamespace(chain=[])))
    assert len(calls["native"]) == 1
    assert calls["context"] == []
    assert event.last_delivery_receipt is None


@pytest.mark.parametrize("scope,origin,error", [
    ("42", "", "task_origin_missing"),
    ("42", "napcat:GroupMessage:43", "task_origin_scope_mismatch"),
    ("42", "napcat:FriendMessage:42", "task_origin_scope_mismatch"),
    ("private:10001", "napcat:GroupMessage:10001", "task_origin_scope_mismatch"),
    ("private:10001", "napcat:FriendMessage:10002", "task_origin_scope_mismatch"),
    ("42", ":GroupMessage:42", "task_origin_scope_mismatch"),
    ("42", "napcat:42", "task_origin_scope_mismatch"),
    ("0", "napcat:GroupMessage:0", "task_origin_scope_mismatch"),
    ("-42", "napcat:GroupMessage:-42", "task_origin_scope_mismatch"),
    ("４２", "napcat:GroupMessage:４２", "task_origin_scope_mismatch"),
])
def test_task_invalid_origin_fails_before_serialization_and_transport(native_delivery, scope, origin, error):
    """Persisted origins cannot be converted into synthetic or cross-scope destinations."""
    plugin, _, calls = native_delivery
    event = TaskEvent(plugin, {"scope": scope, "origin": origin})
    with pytest.raises(RuntimeError, match=f"^{error}$"):
        asyncio.run(event.send(SimpleNamespace(chain=[])))
    assert calls == {"serialized": [], "native": [], "context": []}


@pytest.mark.parametrize("segments", [None, [], "CQ string", [{}], [{"type": "text", "data": None}]])
def test_task_invalid_native_arrays_do_not_send(native_delivery, segments):
    """Empty or malformed serialized output must not generate a false visible delivery."""
    plugin, state, calls = native_delivery
    state["segments"] = segments
    with pytest.raises(RuntimeError, match="^task_message_invalid$"):
        asyncio.run(TaskEvent(plugin, {"scope": "42", "origin": "napcat:GroupMessage:42"}).send(object()))
    assert calls["native"] == calls["context"] == []


def test_task_second_failure_cannot_reuse_previous_receipt(native_delivery):
    """A task event reused for media and final output exposes only the latest outcome."""
    plugin, state, calls = native_delivery
    event = TaskEvent(plugin, {"scope": "42", "origin": "napcat:GroupMessage:42"})
    asyncio.run(event.send(object()))
    assert event.last_delivery_receipt["delivered"] is True
    state["response"] = None
    with pytest.raises(RuntimeError, match="^task_delivery_unconfirmed$"):
        asyncio.run(event.send(object()))
    assert event.last_delivery_receipt is None and len(calls["native"]) == 2


@pytest.mark.parametrize("action,data", [
    ("get_group_root_files", {"files": [{"file_id": "offline", "file_name": "report.txt"}], "folders": []}),
    ("get_group_file_url", {"url": "https://offline.invalid/report.txt"}),
    ("get_forward_msg", {"messages": [{"message_id": 123, "content": []}]}),
    ("get_group_list", [{"group_id": 42}]),
    ("delete_msg", None),
    ("get_group_root_files", {}),
])
def test_task_action_unwraps_confirmed_data_like_native_cqhttp(native_delivery, action, data):
    """Background tools receive native data directly without losing files or URL fields."""
    plugin, state, calls = native_delivery
    state["response"] = {"status": "ok", "retcode": 0, "data": data}
    event = TaskEvent(plugin, {"scope": "42", "origin": "napcat:GroupMessage:42"})
    result = asyncio.run(event.bot.api.call_action(action, group_id=42))
    assert result is data
    assert calls["native"] == [(action, {"group_id": 42})]
    assert calls["context"] == calls["serialized"] == []


@pytest.mark.parametrize("response", [
    None, False, {}, {"ok": True, "data": None},
    {"status": "ok", "retcode": 0},
    {"status": "ok", "retcode": False, "data": {}},
    {"status": "ok", "retcode": "0", "data": {}},
    {"status": "failed", "retcode": 100, "data": {"files": []}},
    {"status": "async", "retcode": 1, "data": {}},
    {"status": "ok", "retcode": 0, "data": {}, "message": "NapCat is running"},
])
def test_task_action_rejects_unconfirmed_envelope_without_empty_success(native_delivery, response):
    """Failed reads and root health greetings cannot masquerade as successful empty data."""
    plugin, state, calls = native_delivery
    state["response"] = response
    event = TaskEvent(plugin, {"scope": "42", "origin": "napcat:GroupMessage:42"})
    with pytest.raises(RuntimeError, match="^task_action_unconfirmed$"):
        asyncio.run(event.bot.api.call_action("get_group_root_files", group_id=42))
    assert calls["native"] == [("get_group_root_files", {"group_id": 42})]
    assert calls["context"] == []


def test_task_action_transport_exception_propagates_without_retry(native_delivery):
    """Read transport failures remain failures and never trigger a second action."""
    plugin, state, calls = native_delivery
    state["response"] = TimeoutError("offline read timeout")
    event = TaskEvent(plugin, {"scope": "42", "origin": "napcat:GroupMessage:42"})
    with pytest.raises(TimeoutError):
        asyncio.run(event.bot.api.call_action("get_group_file_url", group_id=42, file_id="offline"))
    assert len(calls["native"]) == 1 and calls["context"] == []


def test_task_unknown_transport_outcome_is_not_retried(native_delivery):
    """Timeouts propagate once rather than risking duplicate sends through a fallback."""
    plugin, state, calls = native_delivery
    state["response"] = TimeoutError("offline unknown outcome")
    with pytest.raises(TimeoutError):
        asyncio.run(TaskEvent(plugin, {"scope": "42", "origin": "napcat:GroupMessage:42"}).send(object()))
    assert len(calls["native"]) == 1 and calls["context"] == []
