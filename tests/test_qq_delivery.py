"""Pure native delivery contracts: no live QQ calls or plugin runtime imports."""

import asyncio
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def delivery():
    """Import only the delivery helper, without starting the plugin or its clients."""
    path = Path(__file__).resolve().parents[1] / "plugin" / "qq_delivery.py"
    spec = importlib.util.spec_from_file_location("isolated_qq_delivery", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("kind,ids,action", [
    ("group", {"group_id": 456}, "send_group_msg"),
    ("private", {"user_id": 123}, "send_private_msg"),
    ("temp", {"user_id": 123, "group_id": 456}, "send_private_msg"),
])
def test_native_actions_and_literal_text(delivery, kind, ids, action):
    """Keep text, including CQ syntax and whitespace, literal across all destinations."""
    text = "  [CQ:at,qq=999]\n原样  "
    built_action, params = delivery.build(kind, text, **ids)
    assert built_action == action
    assert params == {**ids, "message": [{"type": "text", "data": {"text": text}}]}


def test_group_mentions_are_native_and_ordered(delivery):
    """Mention IDs originate only in the list, never from regex parsing the text."""
    action, params = delivery.build("group", "[CQ:at,qq=999]", group_id=456, mentions=[123, "00456"])
    assert action == "send_group_msg"
    assert params["message"] == [
        {"type": "at", "data": {"qq": "123"}},
        {"type": "at", "data": {"qq": "00456"}},
        {"type": "text", "data": {"text": "[CQ:at,qq=999]"}},
    ]


@pytest.mark.parametrize("kind,args", [
    ("GROUP", {"group_id": 1}), (None, {"group_id": 1}),
    ([], {"group_id": 1}), ("group", {}), ("private", {}), ("temp", {"user_id": 1}),
    ("temp", {"group_id": 1}), ("group", {"group_id": "123"}),
    ("group", {"group_id": True}), ("private", {"user_id": False}),
    ("private", {"user_id": -1}), ("private", {"user_id": 1.0}),
    ("private", {"user_id": 1, "group_id": 2}),
    ("group", {"group_id": 1, "user_id": 2}),
    ("group", {"group_id": 1, "user_id": False}),
    ("group", {"group_id": 1, "mentions": "123"}),
    ("group", {"group_id": 1, "mentions": (123,)}),
    ("private", {"user_id": 1, "mentions": [2]}),
    ("temp", {"user_id": 1, "group_id": 2, "mentions": [3]}),
])
def test_invalid_destinations_or_mentions_never_default(delivery, kind, args):
    """No owner fallback, coercion, foreign destination or private at-segment is allowed."""
    with pytest.raises(ValueError):
        delivery.build(kind, "x", **args)


@pytest.mark.parametrize("value", [True, False, 0, -1, 1.2, None, {}, "", "0", "000", "-1", "+1", " 1", "1.0", "all", "１２３", "[CQ:at,qq=1]"])
def test_invalid_mention_ids(delivery, value):
    """Reject boolean, special, malformed and non-ASCII IDs before sending."""
    with pytest.raises(ValueError):
        delivery.build("group", "x", group_id=1, mentions=[value])


@pytest.mark.parametrize("text", [None, 12, True, {}, []])
def test_text_is_not_coerced(delivery, text):
    """Do not stringify unknown objects into outbound messages."""
    with pytest.raises(ValueError):
        delivery.build("private", text, user_id=1)


@pytest.mark.parametrize("message_id", [123, "123", "00123"])
def test_receipt_requires_native_success_with_message_id(delivery, message_id):
    """A positive ID on a complete native success is the only delivery evidence."""
    assert delivery.receipt({"status": "ok", "retcode": 0, "data": {"message_id": message_id}}, action="send_private_msg") == {
        "ok": True, "delivered": True, "message_id": str(message_id), "action": "send_private_msg",
    }


@pytest.mark.parametrize("response", [
    None, [], "ok", {}, {"message_id": 123},
    {"status": "ok", "retcode": 0, "data": None},
    {"status": "ok", "retcode": 0, "data": {"message_id": True}},
    {"status": "ok", "retcode": 0, "data": {"message_id": 0}},
    {"status": "ok", "retcode": 0, "data": {"message_id": "0"}},
    {"status": "ok", "retcode": 0, "data": {"message_id": "secret"}},
    {"status": "ok", "retcode": False, "data": {"message_id": 123}},
    {"status": "ok", "retcode": "0", "data": {"message_id": 123}},
    {"retcode": 0, "data": {"message_id": 123}},
])
def test_noop_or_malformed_receipts_never_claim_delivery(delivery, response):
    """Regression: root action-envelope ok/null no-ops are not delivered messages."""
    result = delivery.receipt(response, action="send_group_msg")
    assert result["ok"] is False and result["delivered"] is False
    assert result["error"] == "delivery_not_confirmed"
    assert "message_id" not in result and "response" not in result


def test_failed_receipt_is_sanitized(delivery):
    """Keep native numeric codes and known status only, never upstream error content."""
    result = delivery.receipt({"status": "failed", "retcode": 1404, "data": {"message_id": 12}, "message": "secret"}, action="send_private_msg")
    assert result == {"ok": False, "delivered": False, "error": "onebot_action_failed", "action": "send_private_msg", "retcode": 1404, "status": "failed"}
    assert "secret" not in repr(result)
    assert delivery.receipt({"status": "secret", "retcode": "secret"}, action="send_private_msg")["status"] is None


@pytest.mark.parametrize("kind,ids", [("group", {"group_id": 45}), ("private", {"user_id": 12}), ("temp", {"user_id": 12, "group_id": 45})])
def test_send_awaits_native_callback_once(delivery, kind, ids):
    """Use action-specific callback args directly without a root action envelope."""
    calls = []

    async def callback(action, params):
        """Capture only one in-memory call and return native delivery evidence."""
        calls.append((action, params))
        return {"status": "ok", "retcode": 0, "data": {"message_id": 99}}

    result = asyncio.run(delivery.send(kind, "hello", **ids, call_action=callback))
    assert result["delivered"] is True and result["message_id"] == "99"
    assert calls == [delivery.build(kind, "hello", **ids)]


@pytest.mark.parametrize("response,error", [
    ({"status": "ok", "retcode": 0, "data": None}, "delivery_not_confirmed"),
    ({"status": "ok", "retcode": 1200, "data": {"message_id": 9}}, "onebot_action_failed"),
])
def test_send_never_promotes_noop_or_failed_response(delivery, response, error):
    """Exercise the incident envelope through the public async path with no retry."""
    calls = []

    async def callback(action, params):
        """Return a fake native failure or a successful root-router no-op."""
        calls.append((action, params))
        return response

    result = asyncio.run(delivery.send("group", "hello", group_id=1, call_action=callback))
    assert result["ok"] is False and result["delivered"] is False
    assert result["error"] == error and len(calls) == 1


def test_send_exception_is_safe_and_not_retried(delivery):
    """An uncertain send must neither leak exception messages nor risk duplicate sends."""
    calls = []

    async def callback(action, params):
        """Simulate a transport timeout after an unknown delivery outcome."""
        calls.append((action, params))
        raise TimeoutError("secret token and private URL")

    result = asyncio.run(delivery.send("private", "hello", user_id=1, call_action=callback))
    assert result["ok"] is False and result["delivered"] is False
    assert result["exception"] == "TimeoutError" and "secret" not in repr(result)
    assert len(calls) == 1


def test_invalid_send_does_not_call_transport(delivery):
    """Validate before attempting IO, including missing callback and destination."""
    calls = []

    async def callback(action, params):
        """Record a forbidden call if validation regresses."""
        calls.append((action, params))

    with pytest.raises(ValueError):
        asyncio.run(delivery.send("private", "hello", call_action=callback))
    with pytest.raises(ValueError):
        asyncio.run(delivery.send("private", "hello", user_id=1))
    assert calls == []
