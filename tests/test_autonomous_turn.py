"""Isolated native-turn integration checks with no network or AstrBot runtime."""
import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def modules(monkeypatch):
    """Load the integration adapter and pure dependencies in a private package."""
    root = Path(__file__).resolve().parents[1] / "plugin"
    package = ModuleType("isolated_autonomous_package")
    package.__path__ = [str(root)]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    loaded = {}
    for filename in ("task_event", "reply_selection", "autonomy", "autonomous_turn"):
        name = package.__name__ + "." + filename
        spec = importlib.util.spec_from_file_location(name, root / (filename + ".py"))
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        loaded[filename] = module
    return loaded


@pytest.fixture
def setup_turn(modules, tmp_path):
    """Supply the real scheduler and a recording plugin host with explicit clock data."""
    clock = [1000.0]
    controller = modules["autonomy"].AutonomousController(tmp_path / "autonomy.json", now=lambda: clock[0])
    controller.observe("42", "adapter:GroupMessage:42")
    clock[0] = 1600.0
    policy, calls = {}, []
    plugin = SimpleNamespace(_execution_lock=asyncio.Lock(), _pending={}, _stopping=False,
                             _recent={"42": ["actual latest history"]}, _turn_context={},
                             _group_policy=lambda scope: policy, _clock_context=lambda: {"now": clock[0]})

    async def decide(event, scope, text, has_image):
        """Record the unchanged native entry contract and the fully frozen context."""
        assert plugin._execution_lock.locked()
        calls.append((event, scope, text, has_image, dict(plugin._turn_context[scope])))
        return ""

    async def send(event, scope, text, *, decoded=False):
        """Verify one decoder boundary before transport, without external effects."""
        assert decoded is True
        calls.append(("send", scope, text))
        return text

    plugin._decide_tools_first, plugin._send = decide, send
    triggers = controller.opportunities(policy)
    return plugin, controller, triggers, calls, policy, clock


def test_silent_turn_is_not_human_input(modules, setup_turn):
    """Silence is a successful autonomous choice; its source has no human message or @."""
    plugin, controller, triggers, calls, _, _ = setup_turn
    result = asyncio.run(modules["autonomous_turn"].run_turn(plugin, "42", triggers, controller))
    assert result == {"status": "completed", "scope": "42", "delivered": False}
    event, scope, text, has_image, context = calls[0]
    assert scope == "42" and text == "" and has_image is False
    assert context["messages"] == [] and context["history"] == ["actual latest history"]
    assert context["clock"] == {"now": 1600.0}
    assert context["trigger"] == {"kind": "autonomous_opportunity", "events": triggers}
    assert event.get_sender_id() == "autonomous" and event.get_self_id() == ""
    assert event.get_messages() == [] and event.get_message_str() == ""
    assert event.unified_msg_origin == "adapter:GroupMessage:42"
    assert plugin._turn_context == {} and not plugin._execution_lock.locked()
    assert controller.snapshot()["last_started"]["outcome"] == "started"


def test_busy_lock_defers_without_consuming(modules, setup_turn):
    """Foreground work retains priority without losing the due heartbeat."""
    plugin, controller, triggers, calls, _, _ = setup_turn

    async def exercise():
        """Hold the existing native lock across one background scheduling attempt."""
        await plugin._execution_lock.acquire()
        try:
            return await modules["autonomous_turn"].run_turn(plugin, "42", triggers, controller)
        finally:
            plugin._execution_lock.release()

    result = asyncio.run(exercise())
    assert result["status"] == "deferred" and calls == []
    assert controller.opportunities({}) == triggers


def test_pending_chat_defers_without_ack(modules, setup_turn):
    """A queued message need not already own the execution lock to have priority."""
    plugin, controller, triggers, calls, _, _ = setup_turn
    plugin._pending = {"43": ["actual arrival"]}
    result = asyncio.run(modules["autonomous_turn"].run_turn(plugin, "42", triggers, controller))
    assert result["reason"] == "foreground_work" and calls == []
    assert controller.opportunities({}) == triggers


@pytest.mark.parametrize("restriction", ["mention_only", "muted"])
def test_policy_rechecked_after_opportunity(modules, setup_turn, restriction):
    """Changing a saved policy after enumeration cannot authorize background work."""
    plugin, controller, triggers, calls, policy, _ = setup_turn
    policy[restriction] = ["42"]
    result = asyncio.run(modules["autonomous_turn"].run_turn(plugin, "42", triggers, controller))
    assert result["status"] == "blocked" and result["reason"] == "scope_policy"
    assert calls == [] and "last_started" not in controller.snapshot()


def test_unknown_origin_never_uses_fallback_transport(modules, tmp_path):
    """A poll scope without a real framework origin cannot dispatch effectful tools."""
    controller = modules["autonomy"].AutonomousController(tmp_path / "state.json", now=lambda: 1000)
    policy = {"poll_minutes": {"42": 1}}
    plugin = SimpleNamespace(_execution_lock=asyncio.Lock(), _pending={},
                             _group_policy=lambda scope: policy)
    result = asyncio.run(modules["autonomous_turn"].run_turn(
        plugin, "42", controller.opportunities(policy), controller))
    assert result["reason"] == "origin_missing" and not controller.path.exists()


def test_native_failure_keeps_consumption_and_restores_context(modules, setup_turn):
    """Unknown tool outcomes are not replayed and prior context ownership survives."""
    plugin, controller, triggers, _, _, _ = setup_turn
    previous = {"messages": ["original caller object"]}
    plugin._turn_context["42"] = previous

    async def fail(*args):
        """Represent a network exception after an operation may already have happened."""
        raise RuntimeError("unknown external action result")

    plugin._decide_tools_first = fail
    result = asyncio.run(modules["autonomous_turn"].run_turn(plugin, "42", triggers, controller))
    assert result["status"] == "failed" and result["error_type"] == "RuntimeError"
    assert controller.opportunities({}) == []
    assert plugin._turn_context["42"] is previous and not plugin._execution_lock.locked()


def test_cancellation_is_not_swallowed(modules, setup_turn):
    """Shutdown propagates cancellation without making an unknown-effect turn due again."""
    plugin, controller, triggers, _, _, _ = setup_turn

    async def cancel(*args):
        """Represent cancellation of an active native model/tool turn."""
        raise asyncio.CancelledError

    plugin._decide_tools_first = cancel
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(modules["autonomous_turn"].run_turn(plugin, "42", triggers, controller))
    assert plugin._turn_context == {} and controller.opportunities({}) == []
    assert not plugin._execution_lock.locked()


def test_visible_output_uses_existing_sender_once(modules, setup_turn):
    """The helper neither directly calls QQ HTTP nor duplicates history recording."""
    plugin, controller, triggers, calls, _, _ = setup_turn

    async def decide(*args):
        """Represent a native loop's result-aware final text."""
        return "model-chosen result"

    plugin._decide_tools_first = decide
    result = asyncio.run(modules["autonomous_turn"].run_turn(plugin, "42", triggers, controller))
    assert result["delivered"] is True
    assert calls == [("send", "42", "model-chosen result")]


def test_failed_delivery_not_reported_as_success(modules, setup_turn):
    """A policy change or transport failure may suppress actual visible delivery."""
    plugin, controller, triggers, _, _, _ = setup_turn

    async def decide(*args):
        """Produce text that still requires normal confirmed delivery."""
        return "result"

    async def blocked(*args, decoded=False):
        """Represent failed transport after exactly one reply protocol decoding."""
        assert decoded is True
        return None

    plugin._decide_tools_first, plugin._send = decide, blocked
    result = asyncio.run(modules["autonomous_turn"].run_turn(plugin, "42", triggers, controller))
    assert result["status"] == "failed" and result["reason"] == "delivery_not_confirmed"


def test_rescheduled_opportunity_is_not_started(modules, setup_turn):
    """A model plan superseding a listed heartbeat is honored before any model call."""
    plugin, controller, triggers, calls, _, _ = setup_turn
    controller.schedule(next_at=9999)
    result = asyncio.run(modules["autonomous_turn"].run_turn(plugin, "42", triggers, controller))
    assert result["reason"] == "opportunity_changed" and calls == []
    assert "last_started" not in controller.snapshot()


def test_silent_protocol_does_not_call_sender(modules, setup_turn):
    """The shared silence protocol remains a successful no-delivery completion."""
    plugin, controller, triggers, calls, _, _ = setup_turn

    async def silent(*args):
        """Represent explicit native silence rather than forced visible completion."""
        return "SILENT"

    plugin._decide_tools_first = silent
    result = asyncio.run(modules["autonomous_turn"].run_turn(plugin, "42", triggers, controller))
    assert result["status"] == "completed" and result["delivered"] is False and calls == []
