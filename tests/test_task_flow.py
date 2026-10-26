"""Offline contracts for durable tasks reusing the foreground execution/delivery path.

The production entry methods are AST-loaded by the integration fixture. Only
clock ticks, durable-store boundaries, framework delivery, and VM effects are
stubbed: no main import, startup, subprocess, sockets, or live credentials.
"""

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

import test_production_integration as integration
from test_production_integration import Image, MessageChain, _call, _provider
from plugin.task_event import TaskEvent

# Re-export the one shared AST fixture so pytest discovers it without duplicating setup.
production = integration.production


@pytest.mark.parametrize("scope, group", [("42", "42"), ("private:10001", "")])
def test_task_event_is_data_and_sending_adapter_only(scope, group):
    """Stored destinations do not fabricate a human identity or bypass framework send."""
    sent, actions = [], []
    chain = MessageChain().message("offline task answer")

    async def send_message(origin, outgoing):
        """Expose the awaited framework boundary with no actual message transport."""
        sent.append((origin, outgoing))
        return [{"message_id": "task-send-1"}]

    def onebot(action, parameters):
        """Record action forwarding without contacting the local OneBot HTTP service."""
        actions.append((action, parameters))
        return {"ok": True, "nonce": "local-action-result"}

    plugin = SimpleNamespace(context=SimpleNamespace(send_message=send_message), _onebot_http=onebot)
    task = {"scope": scope, "origin": "offline:stored-origin", "resume_prompt": "continue saved work"}
    event = TaskEvent(plugin, task)
    assert event.get_group_id() == group
    assert event.get_sender_id() == "background-task"
    assert event.get_sender_name() == "已保存任务"
    assert event.get_self_id() == ""
    assert event.get_messages() == event.message_obj.message == []
    assert event.message_obj.message_id == ""
    assert event.get_message_str() == "continue saved work"
    assert asyncio.run(event.send(chain)) == [{"message_id": "task-send-1"}]
    assert sent == [(task["origin"], chain)]
    assert asyncio.run(event.bot.api.call_action("get_msg", message_id=1234)) == {
        "ok": True, "nonce": "local-action-result"}
    assert actions == [("get_msg", {"message_id": 1234})]


def test_task_event_missing_origin_and_failed_delivery_never_claim_success():
    """A task with no valid send boundary fails closed rather than returning fake receipts."""
    sent = []

    async def fail_send(origin, chain):
        """Model a framework send rejection without any actual delivery attempt."""
        sent.append(origin)
        raise RuntimeError("offline-framework-rejected")

    plugin = SimpleNamespace(context=SimpleNamespace(send_message=fail_send))
    with pytest.raises(RuntimeError, match="task_origin_missing"):
        asyncio.run(TaskEvent(plugin, {"scope": "42"}).send(MessageChain()))
    assert sent == []
    with pytest.raises(RuntimeError, match="offline-framework-rejected"):
        asyncio.run(TaskEvent(plugin, {"scope": "42", "origin": "offline:42"}).send(MessageChain()))
    assert sent == ["offline:42"]


def test_run_task_once_passes_same_saved_context_to_foreground_path(production):
    """Continuation precedence and the identical task object reach the single native runner."""
    plugin, calls = production(), []
    task = {"id": "own", "scope": "private:10001", "origin": "offline:private",
            "title": "fallback title", "goal": "fallback goal", "resume_prompt": "saved continuation"}

    async def decide(event, scope, text, has_image, task_context=None):
        """Observe routing without creating an alternate task execution implementation."""
        calls.append((event, scope, text, has_image, task_context))
        return "continued once"

    plugin._decide_tools_first = decide
    assert asyncio.run(plugin._run_task_once(task)) == "continued once"
    event, scope, text, has_image, context = calls[0]
    assert isinstance(event, TaskEvent)
    assert event.task is task and context is task
    assert scope == task["scope"] and text == task["resume_prompt"] and has_image is False


def test_task_native_path_reuses_tools_transcript_media_and_saved_goal(production, monkeypatch, tmp_path):
    """Persisted work uses the foreground evidence/media flow, including actual send receipts."""
    plugin, sent, executed = production(), [], []
    media = tmp_path / "task-media.png"
    media.write_bytes(b"offline durable-task media")
    task = {"id": "own", "scope": "42", "origin": "offline:task-origin",
            "goal": "saved-goal-nonce", "resume_prompt": "continue-nonce", "claim_id": "own-claim"}

    async def send_message(origin, chain):
        """Keep framework delivery inspectable and entirely local."""
        sent.append((origin, chain))
        return [{"message_id": "task-media-delivered"}]

    async def dispatch(name, args, scope="", event=None):
        """Replace only the external producer and retain the task event at dispatch."""
        assert isinstance(event, TaskEvent) and event.task is task
        executed.append((name, scope))
        return {"ok": True, "deliver": "plugin", "path": str(media)}

    plugin.context.send_message = send_message
    plugin._tasks_mod = lambda: SimpleNamespace(listing=lambda **kwargs: [dict(task, status="active")])
    plugin._dispatch_tool = dispatch
    saved_context = copy.deepcopy(task)
    requests = _provider(monkeypatch, [("draft", [_call("task_media")]), ("Saved work completed.", [])])
    assert asyncio.run(plugin._run_task_once(task)) == "Saved work completed."
    assert executed == [("task_media", "42")]
    assert task["last_tools"] == ["task_media"]
    assert sent[0][0] == "offline:task-origin"
    assert any(isinstance(component, Image) and component.file == str(media) for component in sent[0][1].chain)
    envelope = json.loads(requests[0]["messages"][1]["content"])
    assert envelope["task"] == saved_context
    assert envelope["task"]["goal"] == "saved-goal-nonce"
    assert envelope["conversation"]["scope"] == "42"
    assert envelope["conversation"]["messages"][0]["body"] == "continue-nonce"
    paired = [message for message in requests[-1]["messages"] if message["role"] == "tool"]
    assert len(paired) == 1 and paired[0]["tool_call_id"] == "task_media"
    record = plugin._tool_evidence().recent("42")[0]
    assert record["result"]["delivered"] is True
    assert plugin._hist().load("42") == ["彼岸酱：[图片]"]


@pytest.mark.parametrize("outcome", ["success", "execution_error", "delivery_error", "cancelled"])
def test_task_loop_claims_before_execution_marks_only_own_task(production, monkeypatch, outcome):
    """Only successfully claimed work is executed/acknowledged, including failure ownership."""
    plugin = production()
    trace, marks = [], []
    task = {"id": "owned", "scope": "42", "origin": "offline:42", "claim_id": "claim-owned"}

    def due():
        """Include a lost claim and another candidate to detect bulk acknowledgement."""
        return [{"id": task_id, "origin": "offline:42"} for task_id in ("lost", "owned", "untouched")]

    def claim(task_id):
        """Represent another executor winning the first candidate before this loop."""
        trace.append(("claim", task_id))
        return copy.deepcopy(task) if task_id == "owned" else None

    def mark(task_id, result, **kwargs):
        """Record exact per-task ownership and stop after a single scheduler iteration."""
        marks.append((task_id, result, kwargs))
        plugin._stopping = True
        return True

    async def run(owned):
        """Assert desktop serialization and claim ownership before the first task effect."""
        assert plugin._execution_lock.locked() and plugin._task_executing
        assert owned["claim_id"] == "claim-owned"
        assert trace == [("claim", "lost"), ("claim", "owned")]
        trace.append(("run", owned["id"]))
        if outcome == "execution_error":
            raise ValueError("offline execution rejected")
        if outcome == "cancelled":
            raise asyncio.CancelledError()
        return "confirmed task answer"

    async def send(event, scope, text, *, decoded=False):
        """Expose delivery ordering and verify task protocol is decoded exactly once."""
        assert decoded is True
        assert isinstance(event, TaskEvent) and event.task["id"] == "owned"
        assert scope == "42" and text == "confirmed task answer"
        trace.append(("send", event.task["id"]))
        return None if outcome == "delivery_error" else text

    async def tick(delay):
        """Advance scheduler time locally without sleeps or periodic background work."""
        assert delay == 25

    plugin._tasks_mod = lambda: SimpleNamespace(due=due, claim=claim, mark=mark)
    plugin._run_task_once, plugin._send = run, send
    monkeypatch.setattr(asyncio, "sleep", tick)
    asyncio.run(plugin._task_loop())
    assert len(marks) == 1
    task_id, result, kwargs = marks[0]
    assert task_id == "owned"
    assert kwargs == {"success": outcome == "success", "claim_id": "claim-owned"}
    expected = {"success": "confirmed task answer", "execution_error": "ValueError",
                "delivery_error": "RuntimeError", "cancelled": "interrupted_outcome_requires_review"}
    assert result == expected[outcome]
    assert plugin._task_executing is False and not plugin._execution_lock.locked()
    assert ("send", "owned") in trace if outcome in {"success", "delivery_error"} else ("send", "owned") not in trace


@pytest.mark.parametrize("blocker", ["pending_chat", "active_execution"])
def test_task_loop_defers_claims_to_waiting_chat_or_active_execution(production, monkeypatch, blocker):
    """A background candidate must not be claimed while foreground work owns the session."""
    plugin, claims = production(), []
    plugin._tasks_mod = lambda: SimpleNamespace(
        due=lambda: [{"id": "deferred", "origin": "offline:42"}], claim=claims.append)
    ticks = []

    async def tick(delay):
        """Stop after inspecting exactly one due batch, without a real timer."""
        ticks.append(delay)
        if len(ticks) > 1:
            plugin._stopping = True

    async def scenario():
        """Hold the actual shared lock or queue throughout the scheduler's first tick."""
        plugin._execution_lock = asyncio.Lock()
        if blocker == "pending_chat":
            plugin._pending["42"] = [("accepted chat",)]
        else:
            await plugin._execution_lock.acquire()
        try:
            await plugin._task_loop()
        finally:
            if plugin._execution_lock.locked():
                plugin._execution_lock.release()

    monkeypatch.setattr(asyncio, "sleep", tick)
    asyncio.run(scenario())
    assert claims == [] and ticks == [25, 25]


def test_task_safe_final_raises_instead_of_successful_acknowledgement(production, monkeypatch):
    """The shared loop's uncertain final result must not count as a completed durable task."""
    from plugin.agent_loop import SAFE_FINAL

    plugin = production()
    task = {"id": "uncertain", "scope": "42", "origin": "offline:42", "goal": "continue saved work"}
    _provider(monkeypatch, [(SAFE_FINAL, [])])
    with pytest.raises(RuntimeError, match="task_finalization_failed"):
        asyncio.run(plugin._run_task_once(task))
    assert plugin._recent == {} and plugin._hist().load("42") == []


def test_task_missing_origin_pauses_before_claim_or_any_effect(production, monkeypatch):
    """Undeliverable legacy task candidates are paused without claiming or running side effects."""
    plugin, paused = production(), []

    def set_status(task_id, **kwargs):
        """Stop the fixture after observing the fail-closed preclaim pause."""
        paused.append((task_id, kwargs))
        plugin._stopping = True

    def forbidden(*args, **kwargs):
        """Any claim or acknowledgement would violate preclaim delivery validation."""
        pytest.fail("task lacking origin reached claim or execution")

    async def tick(delay):
        """Advance one scheduler iteration without waiting for a real clock."""
        assert delay == 25

    plugin._tasks_mod = lambda: SimpleNamespace(
        due=lambda: [{"id": "undeliverable", "scope": "42"}],
        set_status=set_status, claim=forbidden, mark=forbidden)
    plugin._run_task_once = forbidden
    monkeypatch.setattr(asyncio, "sleep", tick)
    asyncio.run(plugin._task_loop())
    assert paused == [("undeliverable", {"status": "paused", "note": "task_origin_missing"})]


def test_ensure_task_loop_starts_only_one_explicit_scheduler(production):
    """Starting durable continuations is idempotent and never enables a free-activity tick."""
    plugin, started = production(), []
    handle = SimpleNamespace(done=lambda: False)

    def start(coroutine):
        """Inspect the selected background coroutine without actually scheduling it."""
        started.append(coroutine.cr_code.co_name)
        coroutine.close()
        return handle

    plugin._start_background = start
    plugin._ensure_task_loop()
    plugin._ensure_task_loop()
    assert started == ["_task_loop"] and plugin._task_loop_task is handle
    handle.done = lambda: True
    plugin._ensure_task_loop()
    assert started == ["_task_loop", "_task_loop"]
