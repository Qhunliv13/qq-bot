"""Offline regressions for structured replies at actual background send boundaries.

Only provider, clock, and platform transport edges are doubled. Production methods,
reply decoding, controller scheduling, TaskEvent transport, and history remain real.
"""

import asyncio
import json
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from test_autonomy_boundaries import _extra_method
from test_production_integration import Event, Reply, _provider
import test_task_flow as task_flow
from plugin.autonomous_turn import run_turn
from plugin.message_batch import message_record
from plugin.task_event import TaskEvent

pytest_plugins = ("test_production_integration",)
# Share the adapter leaf stub; production serialization and receipt handling stay wired.
native_task_serializer = task_flow.native_task_serializer


async def _no_forward(event):
    """Exclude unrelated forward-card transport without bypassing message admission."""
    return ""


@pytest.fixture
def background(production, tmp_path, monkeypatch, native_task_serializer):
    """Keep real send/history/controller logic with isolated local transport and clock."""
    monkeypatch.setenv("HIGAN_ROOT", str(tmp_path))
    monkeypatch.setenv("HIGAN_BATCH_WINDOW", "0")
    plugin = production()
    plugin._is_woken = Mock(return_value=True)
    plugin._wake_hit = Mock(return_value="")
    plugin._forward_to_text = _no_forward
    plugin._mark_replied = Mock(wraps=plugin._mark_replied)
    # These edges must not be reached when a protocol envelope requests silence.
    plugin._deliver_media = AsyncMock(side_effect=AssertionError("unexpected media delivery"))
    plugin._pick_sticker_reviewed = AsyncMock(side_effect=AssertionError("unexpected sticker review"))
    plugin._download_image = AsyncMock(side_effect=AssertionError("unexpected image download"))
    deliveries = []

    def send_native(action, parameters):
        """Confirm one native delivery, retaining the serializer's original chain."""
        assert action in {"send_group_msg", "send_private_msg"}
        private = action == "send_private_msg"
        destination = parameters["user_id" if private else "group_id"]
        origin = "offline-native:%s:%s" % (
            "FriendMessage" if private else "GroupMessage", destination)
        # Serialization captures input, but only this confirmed transport records delivery.
        response = {"status": "ok", "retcode": 0,
                    "data": {"message_id": 73002}}
        deliveries.append((origin, native_task_serializer[-1]))
        return response

    plugin._onebot_http = send_native
    # The shared fixture deliberately loads only selected production methods.
    if not hasattr(plugin, "_autonomy_controller"):
        method, namespace = _extra_method(plugin, "_autonomy_controller")
        namespace["HIGAN_ROOT"] = str(tmp_path)
        plugin._autonomy_controller = MethodType(method, plugin)
    clock = [100000.0]
    controller = plugin._autonomy_controller()
    controller._now = lambda: clock[0]
    origin = "offline-native:GroupMessage:42"
    controller.observe("42", origin)
    clock[0] += 600
    triggers = controller.opportunities(plugin._group_policy("42"))
    assert triggers, "the test must run a real due opportunity, not a deferred turn"
    return SimpleNamespace(plugin=plugin, controller=controller, clock=clock,
                           origin=origin, triggers=triggers, deliveries=deliveries,
                           serialized=native_task_serializer)


def _quiet(native, event=None):
    """Reject transport, bot history, sent IDs, cooldown, and media side effects."""
    plugin = native.plugin
    assert native.deliveries == []
    assert native.serialized == []
    if event is not None:
        assert event.sent == []
        assert event.actions == []
    assert not any(line.startswith("彼岸酱：") for line in plugin._hist().load("42"))
    assert not any(line.startswith("彼岸酱：") for line in plugin._recent.get("42", []))
    assert plugin._last_sent == {}
    assert plugin._last_reply_at == {}
    assert plugin._reply_times == {}
    assert not getattr(plugin, "_my_recent", {})
    plugin._mark_replied.assert_not_called()
    plugin._deliver_media.assert_not_called()
    plugin._pick_sticker_reviewed.assert_not_called()
    plugin._download_image.assert_not_called()


def _body(chain):
    """Expose visible text independently from quote and framework components."""
    return [part.text for part in chain.chain if hasattr(part, "text")]


@pytest.mark.parametrize("text", ["", " ", "SILENT", "silent", "（SILENT）"])
def test_direct_send_empty_unattributed_protocol_has_no_effects(background, monkeypatch, text):
    """No-current direct callers cannot leak an empty/silent JSON envelope as prose."""
    native, event = background, Event()
    requests = _provider(monkeypatch, [])
    raw = json.dumps({"reply_to": [], "text": text})
    assert asyncio.run(native.plugin._send(event, "42", raw)) is None
    _quiet(native, event)
    assert requests == []


@pytest.mark.parametrize("targets", [[9999], [True], "1234"])
@pytest.mark.parametrize("current", [False, True])
def test_direct_send_invalid_attribution_fails_closed(background, targets, current):
    """Unknown or mistyped nonempty targets never become no-current permission."""
    native, event = background, Event()
    if current:
        native.plugin._turn_context["42"] = {"messages": [message_record(event, event.text)]}
    raw = json.dumps({"reply_to": targets, "text": "must not leak"})
    assert asyncio.run(native.plugin._send(event, "42", raw)) is None
    _quiet(native, event)


@pytest.mark.parametrize("text", ["", "SILENT", "（SILENT）"])
def test_task_event_direct_send_suppresses_structured_silence(background, text):
    """Durable-task transport shares the raw protocol guard without current messages."""
    native = background
    event = TaskEvent(native.plugin, {"scope": "42", "origin": native.origin})
    raw = json.dumps({"reply_to": [], "text": text})
    assert asyncio.run(native.plugin._send(event, "42", raw)) is None
    _quiet(native)


def test_task_event_unattributed_text_sends_body_once_without_quote(background):
    """An authentic task destination permits text, not fabricated message attribution."""
    native = background
    event = TaskEvent(native.plugin, {"scope": "42", "origin": native.origin})
    answer = "A local task result."
    raw = json.dumps({"reply_to": [], "text": answer})
    assert asyncio.run(native.plugin._send(event, "42", raw)) == answer
    assert len(native.deliveries) == 1
    origin, chain = native.deliveries[0]
    assert origin == native.origin
    assert _body(chain) == [answer]
    assert not any(isinstance(part, Reply) for part in chain.chain)
    assert native.plugin._hist().load("42") == ["彼岸酱：" + answer]
    native.plugin._mark_replied.assert_called_once_with("42")


@pytest.mark.parametrize("text", ["", "SILENT", "silent", "（SILENT）"])
def test_real_autonomous_turn_structured_silence_completes_undelivered(background, text):
    """A consumed real opportunity succeeds silently rather than leaking JSON or failing."""
    native = background
    native.plugin._decide_tools_first = AsyncMock(
        return_value=json.dumps({"reply_to": [], "text": text}))
    native.plugin._send = AsyncMock(wraps=native.plugin._send)
    result = asyncio.run(run_turn(native.plugin, "42", native.triggers, native.controller))
    assert result == {"status": "completed", "scope": "42", "delivered": False}
    native.plugin._decide_tools_first.assert_awaited_once()
    native.plugin._send.assert_not_awaited()
    _quiet(native)
    assert native.plugin._turn_context == {}
    assert native.controller.snapshot()["last_run"] == native.clock[0]
    assert native.controller.opportunities(native.plugin._group_policy("42")) == []


def test_real_autonomous_turn_unattributed_text_delivers_only_body(background):
    """The actual autonomous sender strips the outer protocol without adding a quote."""
    native = background
    answer = "A local autonomous observation."
    native.plugin._decide_tools_first = AsyncMock(
        return_value=json.dumps({"reply_to": [], "text": answer}))
    result = asyncio.run(run_turn(native.plugin, "42", native.triggers, native.controller))
    assert result == {"status": "completed", "scope": "42", "delivered": True}
    assert len(native.deliveries) == 1
    origin, chain = native.deliveries[0]
    assert origin == native.origin
    assert _body(chain) == [answer]
    assert not any(isinstance(part, Reply) for part in chain.chain)
    assert native.plugin._hist().load("42") == ["彼岸酱：" + answer]
    assert native.plugin._turn_context == {}


@pytest.mark.parametrize("text", ["must not leak", "SILENT"])
def test_real_autonomous_turn_rejects_fabricated_targets(background, text):
    """An observed destination does not supply permission to invent a reply target."""
    native = background
    native.plugin._decide_tools_first = AsyncMock(
        return_value=json.dumps({"reply_to": [1234], "text": text}))
    native.plugin._send = AsyncMock(wraps=native.plugin._send)
    result = asyncio.run(run_turn(native.plugin, "42", native.triggers, native.controller))
    assert result == {"status": "failed", "reason": "reply_protocol_invalid", "scope": "42"}
    native.plugin._send.assert_not_awaited()
    _quiet(native)
    assert native.plugin._turn_context == {}


def test_real_autonomous_turn_preserves_nested_json_literal(background):
    """The autonomous path decodes exactly one envelope before its real shared send."""
    native = background
    literal = json.dumps({"reply_to": [], "text": ""})
    native.plugin._decide_tools_first = AsyncMock(
        return_value=json.dumps({"reply_to": [], "text": literal}))
    result = asyncio.run(run_turn(native.plugin, "42", native.triggers, native.controller))
    assert result == {"status": "completed", "scope": "42", "delivered": True}
    assert len(native.deliveries) == 1
    assert _body(native.deliveries[0][1]) == [literal]
    assert native.plugin._hist().load("42") == ["彼岸酱：" + literal]


@pytest.mark.parametrize("text", ["", "SILENT"])
def test_real_task_continuation_decodes_silence_before_delivery(background, text):
    """Actual durable continuation returns empty text, so its loop cannot send JSON."""
    native = background
    task = {"scope": "42", "origin": native.origin, "resume_prompt": "continue locally"}
    native.plugin._decide_tools_first = AsyncMock(
        return_value=json.dumps({"reply_to": [], "text": text}))
    assert asyncio.run(native.plugin._run_task_once(task)) == ""
    native.plugin._decide_tools_first.assert_awaited_once()
    _quiet(native)


def test_already_decoded_literal_protocol_text_is_not_reparsed(background):
    """A caller's explicit decoded flag preserves a JSON example intended as text."""
    native, event = background, Event()
    literal = json.dumps({"reply_to": [], "text": ""})
    # The positional fourth argument is an existing public compatibility contract.
    assert asyncio.run(native.plugin._send(event, "42", literal, None, decoded=True)) == literal
    assert len(event.sent) == 1
    assert _body(event.sent[0]) == [literal]
    assert native.plugin._hist().load("42") == ["彼岸酱：" + literal]


def test_on_message_nested_json_example_sends_once_as_literal(background, monkeypatch):
    """Current-target foreground decoding must not recursively decode its text field."""
    native, event = background, Event("show this literal JSON example")
    literal = json.dumps({"reply_to": [], "text": ""})
    raw = json.dumps({"reply_to": [1234], "text": literal})
    requests = _provider(monkeypatch, [(raw, [])])
    asyncio.run(native.plugin.on_message(event))
    assert len(requests) == 1
    assert len(event.sent) == 1
    assert _body(event.sent[0]) == [literal]
    # The sole/current-last target does not need an explicit quote component.
    assert not any(isinstance(part, Reply) for part in event.sent[0].chain)
    assert native.plugin._hist().load("42")[-1] == "彼岸酱：" + literal
    assert native.plugin._busy == {"42": False}
    assert native.plugin._turn_context == {}
    assert event.stopped


@pytest.mark.parametrize("targets", [[], [9999], [1234, 9999]])
def test_on_message_nonempty_text_without_valid_current_attribution_is_suppressed(
        background, monkeypatch, targets):
    """Background permission must never weaken foreground current-batch targeting."""
    native, event = background, Event()
    raw = json.dumps({"reply_to": targets, "text": "not attributable"})
    requests = _provider(monkeypatch, [(raw, [])])
    asyncio.run(native.plugin.on_message(event))
    _quiet(native, event)
    assert len(requests) == 1
    assert len(native.plugin._hist().load("42")) == 1
