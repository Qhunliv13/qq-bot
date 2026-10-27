"""Offline contracts for durable tasks reusing the foreground execution/delivery path.

The production entry methods are AST-loaded by the integration fixture. Only
clock ticks, durable-store boundaries, framework delivery, and VM effects are
stubbed: no main import, startup, subprocess, sockets, or live credentials.
"""

import asyncio
import copy
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

import test_production_integration as integration
from test_production_integration import Image, MessageChain, _call, _provider
from plugin.task_event import TaskEvent

# Re-export the one shared AST fixture so pytest discovers it without duplicating setup.
production = integration.production


@pytest.fixture
def native_task_serializer(monkeypatch):
    """Expose the adapter serializer boundary without importing AstrBot or sending IO."""
    chains = []

    async def parse(chain):
        """Record the original framework chain and return native fixture segments."""
        chains.append(chain)
        return [{"type": "text", "data": {"text": "offline serialized message"}}]

    module = ModuleType("astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event")
    module.AiocqhttpMessageEvent = SimpleNamespace(_parse_onebot_json=parse)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return chains


@pytest.mark.parametrize("scope, group", [("42", "42"), ("private:10001", "")])
def test_task_event_is_data_and_sending_adapter_only(scope, group, native_task_serializer):
    """Stored destinations do not fabricate a human identity or bypass framework send."""
    actions = []
    chain = MessageChain().message("offline task answer")

    async def send_message(origin, outgoing):
        """Reject the boolean-only routing API; native receipt transport must be used."""
        pytest.fail("TaskEvent used unconfirmed context.send_message")

    def onebot(action, parameters):
        """Record native action forwarding without contacting any OneBot HTTP service."""
        actions.append((action, parameters))
        if action in {"send_group_msg", "send_private_msg"}:
            return {"status": "ok", "retcode": 0, "data": {"message_id": 72001}}
        return {"ok": True, "nonce": "local-action-result"}

    plugin = SimpleNamespace(context=SimpleNamespace(send_message=send_message), _onebot_http=onebot)
    destination = scope.removeprefix("private:")
    origin = "offline:%s:%s" % ("FriendMessage" if scope.startswith("private:") else "GroupMessage", destination)
    task = {"scope": scope, "origin": origin, "resume_prompt": "continue saved work"}
    event = TaskEvent(plugin, task)
    assert event.get_group_id() == group
    assert event.get_sender_id() == "background-task"
    assert event.get_sender_name() == "已保存任务"
    assert event.get_self_id() == ""
    assert event.get_messages() == event.message_obj.message == []
    assert event.message_obj.message_id == ""
    assert event.get_message_str() == "continue saved work"
    action = "send_private_msg" if scope.startswith("private:") else "send_group_msg"
    receipt = {"ok": True, "delivered": True, "message_id": "72001", "action": action}
    assert asyncio.run(event.send(chain)) == [receipt]
    assert event.last_delivery_receipt == receipt
    assert native_task_serializer == [chain]
    assert actions == [(action, {"user_id" if scope.startswith("private:") else "group_id": int(destination),
                                "message": [{"type": "text", "data": {"text": "offline serialized message"}}]})]
    assert asyncio.run(event.bot.api.call_action("get_msg", message_id=1234)) == {
        "ok": True, "nonce": "local-action-result"}
    assert actions[-1] == ("get_msg", {"message_id": 1234})


def test_task_event_missing_origin_and_failed_delivery_never_claim_success(native_task_serializer):
    """Missing origins and failed native acknowledgements fail closed without fake receipts."""
    actions = []

    def fail_send(action, parameters):
        """Model a native rejection without any actual delivery attempt."""
        actions.append((action, parameters))
        return {"status": "failed", "retcode": 100, "data": None}

    plugin = SimpleNamespace(_onebot_http=fail_send)
    with pytest.raises(RuntimeError, match="task_origin_missing"):
        asyncio.run(TaskEvent(plugin, {"scope": "42"}).send(MessageChain()))
    assert actions == [] and native_task_serializer == []
    with pytest.raises(RuntimeError, match="task_delivery_unconfirmed"):
        asyncio.run(TaskEvent(plugin, {"scope": "42", "origin": "offline:GroupMessage:42"}).send(MessageChain()))
    assert actions[0][0] == "send_group_msg"
    assert len(native_task_serializer) == 1


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


def test_task_native_path_reuses_tools_transcript_media_and_saved_goal(production, monkeypatch, tmp_path,
                                                                     native_task_serializer):
    """Persisted work uses the foreground evidence/media flow, including actual send receipts."""
    plugin, sent, executed = production(), [], []
    media = tmp_path / "task-media.png"
    media.write_bytes(b"offline durable-task media")
    task = {"id": "own", "scope": "42", "origin": "offline:GroupMessage:42",
            "goal": "saved-goal-nonce", "resume_prompt": "continue-nonce", "claim_id": "own-claim"}

    def send_native(action, parameters):
        """Keep the final native receipt transport inspectable and entirely local."""
        sent.append((action, parameters))
        return {"status": "ok", "retcode": 0, "data": {"message_id": 72002}}

    async def dispatch(name, args, scope="", event=None):
        """Replace only the external producer and retain the task event at dispatch."""
        assert isinstance(event, TaskEvent) and event.task is task
        executed.append((name, scope))
        return {"ok": True, "deliver": "plugin", "path": str(media)}

    plugin._onebot_http = send_native
    plugin._tasks_mod = lambda: SimpleNamespace(listing=lambda **kwargs: [dict(task, status="active")])
    plugin._dispatch_tool = dispatch
    saved_context = copy.deepcopy(task)
    requests = _provider(monkeypatch, [("draft", [_call("task_media")]), ("Saved work completed.", [])])
    assert asyncio.run(plugin._run_task_once(task)) == "Saved work completed."
    assert executed == [("task_media", "42")]
    assert task["last_tools"] == ["task_media"]
    assert sent[0][0] == "send_group_msg" and sent[0][1]["group_id"] == 42
    assert any(isinstance(component, Image) and component.file == str(media)
               for component in native_task_serializer[0].chain)
    envelope = json.loads(requests[0]["messages"][1]["content"])
    assert {key: value for key, value in envelope["task"].items() if key != "_delivery_outcome"} == saved_context
    assert envelope["task"]["_delivery_outcome"]["delivery_attempted"] is False
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
        if outcome == "success":
            event.last_delivery_receipt = {"ok": True, "delivered": True,
                                           "message_id": "72003", "action": "send_group_msg"}
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
    assert kwargs["success"] is (outcome == "success") and kwargs["claim_id"] == "claim-owned"
    if outcome in {"success", "delivery_error"}:
        evidence = kwargs["outcome"]
        assert evidence["delivery_attempted"] is True
        assert evidence["delivery_confirmed"] is (outcome == "success")
        assert evidence["delivery_unknown"] is (outcome == "delivery_error")
        assert evidence["delivery_message_ids"] == (["72003"] if outcome == "success" else [])
    else:
        assert "outcome" not in kwargs
    expected = {"success": "confirmed task answer", "execution_error": "ValueError",
                "delivery_error": "RuntimeError", "cancelled": "interrupted_outcome_requires_review"}
    assert result == expected[outcome]
    assert plugin._task_executing is False and not plugin._execution_lock.locked()
    assert ("send", "owned") in trace if outcome in {"success", "delivery_error"} else ("send", "owned") not in trace


@pytest.mark.parametrize("blocker", ["pending_chat", "active_execution"])
def test_task_loop_defers_claims_to_waiting_chat_or_active_execution(production, monkeypatch, blocker):
    """Wait for active execution without claiming; pending chat alone cannot starve due work."""
    plugin, claims = production(), []

    def claim(task_id):
        """Stop after the candidate reaches exclusive ownership, without external effects."""
        assert plugin._execution_lock.locked()
        claims.append(task_id)
        plugin._stopping = True
        return None

    plugin._tasks_mod = lambda: SimpleNamespace(
        due=lambda: [{"id": "deferred", "origin": "offline:42"}], claim=claim)

    async def scenario():
        """Use a queue-entry event rather than timer races, always bounded by wait_for."""
        waiting = asyncio.Event()

        class ObservedLock(asyncio.Lock):
            """Expose an actual contended acquire so the test can release foreground ownership."""

            async def acquire(self):
                """Announce only blocked acquisitions before awaiting the real FIFO lock."""
                if self.locked():
                    waiting.set()
                return await super().acquire()

        async def tick(delay):
            """Advance the native scheduler tick without sleeping or counting polling loops."""
            assert delay == 25

        plugin._execution_lock = ObservedLock()
        plugin._pending["42"] = [("accepted chat",)]
        if blocker == "active_execution":
            await plugin._execution_lock.acquire()
        monkeypatch.setattr(asyncio, "sleep", tick)
        runner = asyncio.create_task(plugin._task_loop())
        try:
            if blocker == "active_execution":
                await asyncio.wait_for(waiting.wait(), timeout=2)
                assert claims == []
                plugin._execution_lock.release()
            await asyncio.wait_for(runner, timeout=2)
        finally:
            if not runner.done():
                runner.cancel()
                await asyncio.gather(runner, return_exceptions=True)
            if plugin._execution_lock.locked():
                plugin._execution_lock.release()

    asyncio.run(scenario())
    assert claims == ["deferred"]


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
