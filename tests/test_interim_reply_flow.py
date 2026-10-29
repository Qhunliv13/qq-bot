"""Exercise explicit model-authored interim replies through the real native flow.

Only model/VM/platform transport edges are doubled. The production dispatcher,
native transcript, protocol decoder, sender, history, and foreground entry stay real.
"""

import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest

from test_production_integration import Event, Reply, _call, _provider
import test_task_flow as task_flow
from plugin.autonomous_turn import AutonomyEvent
from plugin.message_batch import message_record
from plugin.task_event import TaskEvent

pytest_plugins = ("test_production_integration",)
# Reuse the existing leaf-module serializer fixture rather than creating another renderer.
native_task_serializer = task_flow.native_task_serializer


async def _no_forward(event):
    """Exclude unrelated forward-card transport without bypassing real admission."""
    return ""


def _texts(chains):
    """Read only visible text, keeping quote components independently inspectable."""
    return [part.text for chain in chains for part in chain.chain if hasattr(part, "text")]


def _final(text, targets=None, delivery=None):
    """Build a model-authored reply with an optional explicit publication disposition."""
    value = {"reply_to": [1234] if targets is None else targets, "text": text}
    if delivery is not None:
        value["delivery"] = delivery
    return json.dumps(value)


@pytest.fixture
def interim(production, tmp_path, monkeypatch, native_task_serializer):
    """Keep production methods while isolating only external transport and wake edges."""
    monkeypatch.setenv("HIGAN_ROOT", str(tmp_path))
    monkeypatch.setenv("HIGAN_BATCH_WINDOW", "0")
    plugin = production()
    plugin.persona = "Custom sender persona: speak precisely in your own words."
    plugin._is_woken = Mock(return_value=True)
    plugin._wake_hit = Mock(return_value="")
    plugin._forward_to_text = _no_forward
    plugin._deliver_media = AsyncMock(side_effect=AssertionError("unexpected media"))
    plugin._pick_sticker_reviewed = AsyncMock(side_effect=AssertionError("unexpected sticker"))
    plugin._download_image = AsyncMock(side_effect=AssertionError("unexpected download"))
    plugin._vm_tool = AsyncMock(return_value={"ok": True, "files": ["measurement-17.txt"]})
    plugin._native_deliveries = []

    def send_native(action, parameters):
        """Record receipt-confirmed native sends without replacing TaskEvent.send."""
        assert action in {"send_group_msg", "send_private_msg"}
        private = action == "send_private_msg"
        destination = parameters["user_id" if private else "group_id"]
        origin = "offline-native:%s:%s" % (
            "FriendMessage" if private else "GroupMessage", destination)
        # Keep the original chain inspectable; serialization itself is not delivery.
        response = {"status": "ok", "retcode": 0,
                    "data": {"message_id": 73001}}
        plugin._native_deliveries.append((origin, native_task_serializer[-1]))
        return response

    plugin._onebot_http = send_native
    return plugin


def _bot_history(plugin):
    """Select confirmed outgoing entries, not the recorded incoming user message."""
    return [line for line in plugin._hist().load("42") if line.startswith("彼岸酱：")]


def test_model_chosen_ack_progress_finish_share_one_native_transcript(interim, monkeypatch):
    """Three dynamic bodies send in order; tool-bearing thoughts never auto-publish."""
    plugin, event = interim, Event("Inspect the local measurements")
    first = "I will inspect measurement seventeen."
    progress = "The local inventory contains measurement seventeen."
    final = "Measurement seventeen is available in the local inventory."
    calls = [_call("reply_now", {"text": first}, "public-first"),
             _call("vm_files", {"dir": "measurements"}, "inventory"),
             _call("reply_now", {"text": progress, "reply_to": ["1234"]}, "public-progress")]
    requests = _provider(monkeypatch, [
        ("private planning alpha", [calls[0]]),
        ("private planning beta", [calls[1]]),
        ("private planning gamma", [calls[2]]),
        (_final(final, delivery="new"), []),
    ])
    observed = []

    async def inventory(name, args):
        """Prove the first public send completed before the next tool executed."""
        observed.append((_texts(event.sent), list(plugin._turn_context["42"]["_interim_replies"])))
        assert (name, args) == ("vm_files", {"dir": "measurements"})
        return {"ok": True, "files": ["measurement-17.txt"]}

    plugin._vm_tool.side_effect = inventory
    asyncio.run(plugin.on_message(event))
    assert observed == [([first], [first])]
    assert _texts(event.sent) == [first, progress, final]
    assert _bot_history(plugin) == ["彼岸酱：" + text for text in (first, progress, final)]
    assert len(requests) == 4
    schemas = requests[0]["tools"]
    names = [tool["function"]["name"] for tool in schemas]
    from plugin.tool_catalog import NATIVE_TOOL_COUNT
    assert len(names) == len(set(names)) == NATIVE_TOOL_COUNT
    assert names.count("reply_now") == 1
    assert all(request["tools"] == schemas for request in requests)
    assert all(plugin.persona in request["messages"][0]["content"] for request in requests)
    transcript = requests[-1]["messages"]
    assert [message["role"] for message in transcript] == [
        "system", "user", "assistant", "tool", "assistant", "tool", "assistant", "tool"]
    paired = [message for message in transcript if message["role"] == "tool"]
    assert [message["tool_call_id"] for message in paired] == [call["id"] for call in calls]
    assert [json.loads(message["content"])["tool_name"] for message in paired] == [
        "reply_now", "vm_files", "reply_now"]
    results = [json.loads(message["content"])["result"] for message in paired]
    assert results[0]["delivered"] is results[2]["delivered"] is True
    assert results[0]["text"] == first and results[2]["text"] == progress
    assert results[1] == {"ok": True, "files": ["measurement-17.txt"]}
    # Snapshots are immutable append-only provider requests, not independently rewritten prompts.
    assert requests[1]["messages"] == transcript[:4]
    assert requests[2]["messages"] == transcript[:6]
    assert plugin._turn_context == {} and plugin._busy == {"42": False}
    assert event.stopped
    assert not any(isinstance(part, Reply) for chain in event.sent for part in chain.chain)
    plugin._pick_sticker_reviewed.assert_not_awaited()


@pytest.mark.parametrize("suffix,expected_count", [("", 1), ("  ", 1), (" Additional detail.", 1)])
def test_untyped_final_stays_internal_after_public_reply(interim, monkeypatch, suffix, expected_count):
    """Omitted publication intent stays internal regardless of textual similarity."""
    plugin, event = interim, Event()
    body = "The local report contains seventeen entries."
    requests = _provider(monkeypatch, [
        ("do not publish this thought", [_call("reply_now", {"text": body})]),
        (_final(body + suffix), []),
    ])
    asyncio.run(plugin.on_message(event))
    assert len(requests) == 2
    assert len(event.sent) == expected_count
    assert _texts(event.sent) == ([body] if expected_count == 1 else [body, body + suffix])
    assert len(_bot_history(plugin)) == expected_count


def test_tool_call_content_without_reply_now_is_never_published(interim, monkeypatch):
    """Ordinary tool-bearing assistant content remains private even when nonempty."""
    plugin, event = interim, Event()
    requests = _provider(monkeypatch, [
        ("This is private tool deliberation.", [_call("vm_files")]),
        (_final("The inventory query is complete."), []),
    ])
    asyncio.run(plugin.on_message(event))
    assert len(requests) == 2
    assert _texts(event.sent) == ["The inventory query is complete."]
    assert _bot_history(plugin) == ["彼岸酱：The inventory query is complete."]


@pytest.mark.parametrize("targets", [["9999"], [True], [1234], "1234", []])
def test_invalid_explicit_target_fails_closed_and_final_still_sends(interim, monkeypatch, targets):
    """Bad current-batch attribution neither sends nor manufactures successful evidence."""
    plugin, event = interim, Event()
    requests = _provider(monkeypatch, [
        ("private thought", [_call("reply_now", {"text": "No permission to publish.", "reply_to": targets})]),
        (_final("The attributable final result remains available."), []),
    ])
    asyncio.run(plugin.on_message(event))
    result = json.loads(requests[-1]["messages"][-1]["content"])["result"]
    assert result["ok"] is False and result["delivered"] is False
    assert "text" not in result
    assert _texts(event.sent) == ["The attributable final result remains available."]
    assert len(_bot_history(plugin)) == 1


@pytest.mark.parametrize("text", ["", " ", "SILENT", "（SILENT）"])
def test_silent_interim_has_no_delivery_or_bot_history(interim, monkeypatch, text):
    """Explicit silence is a no-op while the same native transcript can continue."""
    plugin, event = interim, Event()
    requests = _provider(monkeypatch, [
        ("private", [_call("reply_now", {"text": text})]),
        ("SILENT", []),
    ])
    asyncio.run(plugin.on_message(event))
    result = json.loads(requests[-1]["messages"][-1]["content"])["result"]
    assert result == {"ok": True, "delivered": False}
    assert event.sent == [] and _bot_history(plugin) == []
    assert plugin._last_sent == {} and plugin._last_reply_at == {}


def test_failed_interim_is_not_success_evidence_and_final_is_attempted_once(interim, monkeypatch):
    """A failed platform send does not poison de-duplication or fabricate bot history."""
    plugin = interim
    attempts = []

    class FailFirstEvent(Event):
        """Fail only the explicit interim edge, leaving the final retry observable."""

        async def send(self, chain):
            """Model a definite failed first send and a confirmed second transport."""
            attempts.append(_texts([chain]))
            if len(attempts) == 1:
                raise RuntimeError("offline definite send failure")
            return await super().send(chain)

    event = FailFirstEvent()
    body = "The final remains eligible after failed delivery."
    requests = _provider(monkeypatch, [
        ("private", [_call("reply_now", {"text": body})]), (_final(body), []),
    ])
    asyncio.run(plugin.on_message(event))
    result = json.loads(requests[-1]["messages"][-1]["content"])["result"]
    assert result["ok"] is False and result["delivered"] is False
    assert "text" not in result
    assert attempts == [[body], [body]]
    assert _texts(event.sent) == [body]
    assert _bot_history(plugin) == ["彼岸酱：" + body]
    assert plugin._last_sent == {"42": ["sent-1"]}


def test_interim_literal_json_is_decoded_once_and_exact_final_is_not_duplicated(interim, monkeypatch):
    """JSON that happens to resemble the outer protocol remains model-authored text."""
    plugin, event = interim, Event()
    literal = json.dumps({"reply_to": [], "text": ""})
    requests = _provider(monkeypatch, [
        ("private", [_call("reply_now", {"text": literal})]), (_final(literal), []),
    ])
    asyncio.run(plugin.on_message(event))
    assert len(requests) == 2 and _texts(event.sent) == [literal]
    assert _bot_history(plugin) == ["彼岸酱：" + literal]


@pytest.mark.parametrize("kind", ["task", "autonomy"])
@pytest.mark.parametrize("targets,delivered", [(None, True), (["1234"], False)])
def test_no_batch_direct_dispatch_uses_real_destination_not_fabricated_targets(interim, kind, targets, delivered):
    """Typed background events may send unattributed text, never invented message IDs."""
    plugin = interim
    outgoing = plugin._native_deliveries
    origin = "offline-native:GroupMessage:42"
    event = (TaskEvent(plugin, {"scope": "42", "origin": origin}) if kind == "task"
             else AutonomyEvent(plugin, "42", origin, []))
    args = {"text": "An unattributed local background observation."}
    if targets is not None:
        args["reply_to"] = targets
    result = asyncio.run(plugin._dispatch_tool("reply_now", args, "42", event=event))
    assert result["delivered"] is delivered
    assert len(outgoing) == int(delivered)
    assert len(_bot_history(plugin)) == int(delivered)
    # Direct calls without a live turn must not manufacture persistent turn state.
    assert plugin._turn_context == {}
    if delivered:
        assert outgoing[0][0] == origin
        assert _texts([outgoing[0][1]]) == [args["text"]]
        assert not any(isinstance(part, Reply) for part in outgoing[0][1].chain)


def test_muted_policy_blocks_explicit_interim_without_ledger_success(interim, monkeypatch, tmp_path):
    """The existing shared sender's policy is authoritative even for explicit tools."""
    from plugin.group_policy import GroupPolicyStore

    plugin, event = interim, Event()
    GroupPolicyStore(tmp_path / "groups.json").update("42", "muted")
    plugin._turn_context["42"] = {"messages": [message_record(event, event.text)]}
    _provider(monkeypatch, [])
    result = asyncio.run(plugin._dispatch_tool("reply_now", {"text": "Must remain private."}, "42", event=event))
    assert result["ok"] is False and result["delivered"] is False
    assert event.sent == [] and _bot_history(plugin) == []
    assert plugin._turn_context["42"].get("_interim_replies", []) == []


def test_interim_quote_targets_real_earlier_batch_message_without_changing_final_quote(interim, monkeypatch):
    """Interim attribution may quote an earlier current message, not rewrite the final target."""
    plugin, event = interim, Event()
    earlier = Event("An earlier current-batch question", message_id="1111")
    plugin._turn_context["42"] = {"messages": [
        message_record(earlier, earlier.text), message_record(event, event.text)]}
    requests = _provider(monkeypatch, [
        ("private", [_call("reply_now", {"text": "The earlier question is being checked.",
                                         "reply_to": ["1111"]})]),
        (_final("The current question now has a distinct result.", delivery="new"), []),
    ])
    asyncio.run(plugin._handle_one(event, "42", event.text, False, True))
    assert len(requests) == 2 and len(event.sent) == 2
    first_quotes = [part.id for part in event.sent[0].chain if isinstance(part, Reply)]
    assert first_quotes == ["1111"]
    assert not any(isinstance(part, Reply) for part in event.sent[1].chain)
    assert plugin._turn_context["42"]["_interim_replies"] == ["The earlier question is being checked."]


def test_prior_turn_delivery_never_suppresses_next_turn_final(interim, monkeypatch):
    """Delivery history is not a global duplicate blacklist; the ledger belongs to one turn."""
    plugin = interim
    first, second = Event(), Event("Please repeat the measured result", message_id="5678")
    body = "The measured local result is seventeen."
    requests = _provider(monkeypatch, [
        ("private", [_call("reply_now", {"text": body})]),
        ("SILENT", []),
        (_final(body, [5678]), []),
    ])
    asyncio.run(plugin.on_message(first))
    assert plugin._turn_context == {}
    asyncio.run(plugin.on_message(second))
    assert len(requests) == 3
    assert _texts(first.sent) == _texts(second.sent) == [body]
    assert _bot_history(plugin) == ["彼岸酱：" + body, "彼岸酱：" + body]


def test_background_native_final_guard_suppresses_confirmed_exact_duplicate(interim, monkeypatch):
    """Callers outside foreground handling share same-turn exact final suppression."""
    plugin = interim
    event = TaskEvent(plugin, {"scope": "42", "origin": "offline-native:GroupMessage:42"})
    delivered = plugin._native_deliveries
    body = "A background result already delivered explicitly."
    requests = _provider(monkeypatch, [
        ("private", [_call("reply_now", {"text": body})]), (_final(body, []), []),
    ])
    result = asyncio.run(plugin._decide_tools_first(event, "42", "", False))
    assert result == ""
    assert _texts([chain for _, chain in delivered]) == [body]
    assert _bot_history(plugin) == ["彼岸酱：" + body]
    assert len(requests) == 2
