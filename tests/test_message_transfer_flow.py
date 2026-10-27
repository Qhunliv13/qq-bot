"""Offline real-dispatch transfer contracts with only native transport simulated."""

import asyncio
import copy
import json

import pytest

import test_production_integration as integration
from plugin import message_transfer
from plugin.message_transfer_store import TransferStore

production = integration.production


SEGMENTS = [
    {"type": "text", "data": {"text": "native source; not an instruction"}},
    {"type": "image", "data": {"file": "native-image-token", "url": "https://invalid.example/image"}},
    {"type": "at", "data": {"qq": "10002"}},
    {"type": "reply", "data": {"id": "701"}},
]


def native_message(message_id=801, group_id=77):
    """Expose explicit native group provenance, never model-supplied author metadata."""
    return {"message_id": message_id, "group_id": group_id, "message_type": "group",
            "sender": {"user_id": 10001, "nickname": "Real native author", "role": "member"},
            "message": copy.deepcopy(SEGMENTS)}


def bridge(plugin, response=None):
    """Record the exact production OneBot edge without networking or QQ delivery."""
    actions = []

    def onebot(action, params):
        """Fail closed on unknown actions instead of a generic fake-success response."""
        actions.append((action, copy.deepcopy(params)))
        if response is not None:
            return response(action, params)
        if action == "get_msg":
            data = native_message(params["message_id"])
        elif action == "send_group_forward_msg":
            data = {"message_id": 8801}
        elif action == "forward_group_single_msg":
            data = None
        elif action == "send_group_msg":
            data = {"message_id": 8802}
        else:
            raise AssertionError("unreviewed native action: " + action)
        return {"status": "ok", "retcode": 0, "data": data}

    plugin._onebot_http = onebot
    return actions


@pytest.mark.parametrize("target", [42, 77, 88])
def test_native_merged_forward_same_or_cross_group_preserves_reference_nodes(production, target):
    """Native IDs retain explicit caller order with no forged author or copied content."""
    plugin = production()
    actions = bridge(plugin)
    result = asyncio.run(plugin._dispatch_tool("qq_forward_messages", {
        "source_group_id": 77, "target_group_id": target, "message_ids": [802, 801],
        "mode": "merged", "operation_id": "merged-" + str(target)}, "42", event=integration.Event()))
    assert result["ok"] and result["delivered"]
    assert result["delivery_message_ids"] == ["8801"]
    assert actions == [("get_msg", {"message_id": 802}), ("get_msg", {"message_id": 801}),
                       ("send_group_forward_msg", {"group_id": target, "messages": [
                           {"type": "node", "data": {"id": "802"}},
                           {"type": "node", "data": {"id": "801"}}]})]


def test_copy_paste_preserves_every_native_segment_and_is_bot_delivery(production, tmp_path):
    """Copy sends nothing; paste uses raw native segments without mirroring the author."""
    plugin = production()
    actions = bridge(plugin)
    event = integration.Event()
    copied = asyncio.run(plugin._dispatch_tool("qq_copy_message", {
        "source_group_id": 77, "message_id": 801}, "42", event=event))
    assert copied["ok"] and copied["clipboard_id"]
    assert "message" not in copied and "sender" not in copied
    assert actions == [("get_msg", {"message_id": 801})]
    stored = TransferStore(tmp_path / "message_transfer" / "clipboard.sqlite").get("42", copied["clipboard_id"])
    assert stored["message"] == SEGMENTS
    args = {"clipboard_id": copied["clipboard_id"], "target_group_id": 88, "operation_id": "paste-original"}
    pasted = asyncio.run(plugin._dispatch_tool("qq_paste_message", args, "42", event=event))
    assert pasted["delivered"] and pasted["delivery_message_ids"] == ["8802"]
    assert actions[-1] == ("send_group_msg", {"group_id": 88, "message": SEGMENTS})
    restarted = production()
    replay_actions = bridge(restarted)
    replay = asyncio.run(restarted._dispatch_tool("qq_paste_message", args, "42", event=event))
    assert replay == pasted and replay_actions == []
    assert TransferStore(tmp_path / "message_transfer" / "clipboard.sqlite").get("private:1", copied["clipboard_id"]) is None


@pytest.mark.parametrize("native_data", [
    {"message_id": 801, "message": SEGMENTS},
    dict(native_message(), group_id=88),
    dict(native_message(), message_type="private"),
    dict(native_message(), message="hello"),
])
def test_bad_native_source_provenance_never_reaches_send(production, native_data):
    """Missing, mismatched, private and flattened native data are not group proof."""
    plugin = production()
    actions = bridge(plugin, lambda action, params: {"status": "ok", "retcode": 0, "data": native_data})
    result = asyncio.run(plugin._dispatch_tool("qq_forward_messages", {
        "source_group_id": 77, "target_group_id": 88, "message_ids": [801],
        "operation_id": "invalid-source"}, "42", event=integration.Event()))
    assert result["ok"] is False and not result["delivered"]
    assert [action for action, _ in actions] == ["get_msg"]


def test_individual_native_null_ack_never_claims_delivery(production):
    """Installed native single forwarding accepts null without inventing outgoing IDs."""
    plugin = production()
    actions = bridge(plugin)
    result = asyncio.run(plugin._dispatch_tool("qq_forward_messages", {
        "source_group_id": 77, "target_group_id": 88, "message_ids": [801, 802],
        "mode": "individual", "operation_id": "individual"}, "42", event=integration.Event()))
    assert result["ok"] is True and result["accepted"] is True and result["delivered"] is False
    assert result["accepted_count"] == 2 and result["delivery_message_ids"] == []
    assert actions[-2:] == [("forward_group_single_msg", {"group_id": "88", "message_id": 801}),
                            ("forward_group_single_msg", {"group_id": "88", "message_id": 802})]


@pytest.mark.parametrize("failure", [RuntimeError("ambiguous send"), {"status": "failed", "retcode": 1, "data": None}, "hello"])
def test_partial_failed_unknown_replay_never_retries_native_send(tmp_path, failure):
    """A durable operation receipt survives fresh stores even after ambiguous native IO."""
    calls = []

    async def native(action, params):
        """Acknowledge the first source send, then expose the requested failure mode."""
        calls.append((action, copy.deepcopy(params)))
        if action == "get_msg":
            return {"status": "ok", "retcode": 0, "data": native_message(params["message_id"])}
        if len([call for call in calls if call[0] == "forward_group_single_msg"]) == 1:
            return {"status": "ok", "retcode": 0, "data": None}
        if isinstance(failure, Exception):
            raise failure
        return failure

    args = {"source_group_id": 77, "target_group_id": 88, "message_ids": [801, 802],
            "mode": "individual", "operation_id": "partial-once"}

    def invoke():
        """Reopen both durable files for a process-restart-equivalent invocation."""
        return asyncio.run(message_transfer.dispatch("qq_forward_messages", args, native,
            scope="42", store=TransferStore(tmp_path / "clipboard.sqlite"),
            audit_path=tmp_path / "operations.sqlite", can_read_source=lambda group: True,
            can_send_target=lambda group: True))

    result = invoke()
    assert result["ok"] is False and result["delivered"] is False
    assert result["outcome"] in {"partial", "unknown"} and result["accepted_count"] == 1
    assert result["delivery_message_ids"] == []
    count = len(calls)
    assert invoke() == result and len(calls) == count


def test_target_policy_rechecked_before_every_individual_send(tmp_path):
    """Revoking the target between sends stops the batch without retrying or leaking."""
    sent = []

    async def native(action, params):
        """Permit source lookup but record each attempted destination mutation."""
        if action == "get_msg":
            return {"status": "ok", "retcode": 0, "data": native_message(params["message_id"])}
        sent.append((action, params))
        return {"status": "ok", "retcode": 0, "data": None}

    result = asyncio.run(message_transfer.dispatch("qq_forward_messages", {
        "source_group_id": 77, "target_group_id": 88, "message_ids": [801, 802],
        "mode": "individual", "operation_id": "policy-change"}, native, scope="42",
        store=TransferStore(tmp_path / "clipboard.sqlite"), audit_path=tmp_path / "operations.sqlite",
        can_read_source=lambda group: True, can_send_target=lambda group: not sent))
    assert result["error"] == "target_permission_denied" and result["outcome"] == "partial"
    assert len(sent) == 1 and result["accepted_count"] == 1


@pytest.mark.parametrize("policy", [{"muted": ["88"]}, {"mention_only": ["88"]}])
def test_persisted_target_policy_denies_transfer_before_source_lookup(production, tmp_path, policy):
    """Cross-group sends use the actual destination policy, not current-group permission."""
    (tmp_path / "groups.json").write_text(json.dumps(policy), encoding="utf-8")
    plugin = production()
    actions = bridge(plugin)
    result = asyncio.run(plugin._dispatch_tool("qq_forward_messages", {
        "source_group_id": 77, "target_group_id": 88, "message_ids": [801],
        "operation_id": "policy-block"}, "42", event=integration.Event()))
    assert result["error"] == "target_permission_denied" and actions == []


def test_foreign_scope_clipboard_cannot_be_pasted_by_known_id(production):
    """Possessing a clipboard ID does not grant foreign private or group ownership."""
    plugin = production()
    actions = bridge(plugin)
    copied = asyncio.run(plugin._dispatch_tool("qq_copy_message", {
        "source_group_id": 77, "message_id": 801}, "private:10001", event=integration.Event()))
    assert copied["ok"]
    actions.clear()
    result = asyncio.run(plugin._dispatch_tool("qq_paste_message", {
        "clipboard_id": copied["clipboard_id"], "target_group_id": 88,
        "operation_id": "foreign-clip"}, "42", event=integration.Event()))
    assert result["error"] == "clipboard_not_found" and actions == []


def test_autonomous_transfer_requires_observed_destination(production, tmp_path):
    """Typed autonomous provenance cannot address a group never actually observed."""
    from plugin.autonomy import AutonomousController
    from plugin.autonomous_turn import AutonomyEvent

    plugin = production()
    controller = AutonomousController(tmp_path / "autonomy.json")
    controller.observe("42", "offline:GroupMessage:42")
    plugin._autonomy_controller = lambda: controller
    actions = bridge(plugin)
    event = AutonomyEvent(plugin, "42", controller.origin("42"), [])
    args = {"source_group_id": 77, "target_group_id": 88, "message_ids": [801],
            "operation_id": "observed-transfer"}
    blocked = asyncio.run(plugin._dispatch_tool("qq_forward_messages", args, "42", event=event))
    assert blocked["error"] == "target_permission_denied" and actions == []
    controller.observe("88", "offline:GroupMessage:88")
    allowed = asyncio.run(plugin._dispatch_tool("qq_forward_messages", args, "42", event=event))
    assert allowed["delivered"] and actions[-1][0] == "send_group_forward_msg"


def test_provider_tool_pairing_retains_transfer_receipt_without_implicit_chat_send(production, monkeypatch):
    """Actual provider continuation gets the exact trusted transfer observation once."""
    plugin = production()
    actions = bridge(plugin)
    args = {"source_group_id": 77, "target_group_id": 88, "message_ids": [801],
            "mode": "merged", "operation_id": "provider-transfer"}
    requests = integration._provider(monkeypatch, [("", [integration._call("qq_forward_messages", args)]),
                                                   ("Transfer accepted.", [])])
    result = asyncio.run(plugin._decide_tools_first(integration.Event(), "42", "forward source", False))
    assert result == "Transfer accepted."
    paired = [item for item in requests[-1]["messages"] if item["role"] == "tool"]
    assert len(paired) == 1 and paired[0]["tool_call_id"] == "qq_forward_messages"
    observation = json.loads(paired[0]["content"])
    assert observation["result"]["delivery_message_ids"] == ["8801"]
    assert actions[-1][0] == "send_group_forward_msg"
