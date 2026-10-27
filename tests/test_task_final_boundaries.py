"""Real task entry boundaries reject cross-turn output and revoked claims offline."""

import asyncio
from types import SimpleNamespace

import pytest
import test_production_integration as integration
from plugin.task_delivery import current_task, task_active
from plugin.task_event import TaskEvent

production = integration.production


def owned_task():
    """Return an isolated persisted claim, never a fabricated human chat turn."""
    return {"id": "task-one", "scope": "42", "origin": "offline:42",
            "claim_id": "owned", "status": "active", "goal": "Search a meme"}


@pytest.mark.parametrize("rows", [[], [{"id": "other", "status": "active"}],
    [{"id": "task-one", "status": "done", "claim_id": "owned"}],
    [{"id": "task-one", "status": "paused", "claim_id": "owned"}],
    [{"id": "task-one", "status": "active", "claim_id": "new-owner"}],
    [{"id": "task-one", "status": "active"}]])
def test_live_claim_fails_closed(rows):
    """No missing row, inactive state or replacement claim authorizes delivery."""
    assert task_active(owned_task(), rows) is False


def test_current_claim_exact_id_and_active():
    """Only one exact ID with its retained claim can authorize continuation."""
    task = owned_task()
    assert current_task(task, [dict(task)]) == task
    assert task_active(task, [dict(task)]) is True
    assert current_task(task, [dict(task), dict(task)]) is None


@pytest.mark.parametrize("status", ["done", "paused", "cancelled", "missing", "reclaimed"])
def test_run_discards_late_final_and_restores_human_turn(production, status):
    """A model result after task revocation cannot publish an unrelated queued reply."""
    plugin = production()
    task = owned_task()
    rows = [dict(task)]
    previous = {"messages": [{"id": "human", "text": "才没拟人喵"}],
                "image_refs": ["human-image"], "trigger": "human"}
    plugin._turn_context = {"42": previous}
    plugin._tasks_mod = lambda: SimpleNamespace(listing=lambda **kwargs: rows)

    async def decide(event, scope, text, has_image, task_context=None):
        """Observe actual context isolation, then revoke while the model is in flight."""
        turn = plugin._turn_context[scope]
        assert turn is not previous
        assert turn["messages"] == turn["image_refs"] == turn["image_sources"] == []
        assert "trigger" not in turn and text == task["goal"]
        if status == "missing":
            rows.clear()
        elif status == "reclaimed":
            rows[0]["claim_id"] = "new-owner"
        else:
            rows[0]["status"] = status
        return "unrelated queued human reply"

    plugin._decide_tools_first = decide
    assert asyncio.run(plugin._run_task_once(task)) == ""
    assert plugin._turn_context["42"] is previous


@pytest.mark.parametrize("terminal", ["done", "paused"])
def test_native_batch_stops_next_model_and_blocks_sibling_send(production, monkeypatch, terminal):
    """A completed tool batch makes no extra final request or outbound sibling call."""
    plugin = production()
    task = owned_task()
    rows = [dict(task)]
    executed = []
    plugin._tasks_mod = lambda: SimpleNamespace(listing=lambda **kwargs: rows)

    async def dispatch(name, args, scope="", event=None):
        """Complete native work before revocation without aborting an in-flight operation."""
        executed.append(name)
        rows[0]["status"] = terminal
        return {"ok": True}

    plugin._dispatch_tool = dispatch
    name = "task_done" if terminal == "done" else "native_operation"
    requests = integration._provider(monkeypatch, [("draft", [
        integration._call(name, {"id": task["id"]}),
        integration._call("qq_send", {"text": "forbidden sibling"})])])
    assert asyncio.run(plugin._decide_tools_first(
        TaskEvent(plugin, task), "42", task["goal"], False, task_context=task)) == ""
    assert len(requests) == 1
    assert executed == [name]
    observations = plugin._tool_evidence().recent("42")
    assert observations[-1]["result"]["error"] == "task_not_active"


def test_run_restores_context_on_error(production):
    """Task exceptions must not erase or mutate the foreground turn waiting behind it."""
    plugin = production()
    task = owned_task()
    previous = {"messages": [{"text": "queued human"}]}
    plugin._turn_context = {"42": previous}
    plugin._tasks_mod = lambda: SimpleNamespace(listing=lambda **kwargs: [task])

    async def decide(*args, **kwargs):
        """Fail within the native entry boundary without any external request."""
        raise RuntimeError("offline failure")

    plugin._decide_tools_first = decide
    with pytest.raises(RuntimeError, match="offline failure"):
        asyncio.run(plugin._run_task_once(task))
    assert plugin._turn_context["42"] is previous


@pytest.mark.parametrize("failure", ["exception", "cancelled"])
@pytest.mark.parametrize("state", ["done", "paused", "reclaimed"])
def test_scheduler_revocation_preserves_metadata_on_error(production, monkeypatch, failure, state):
    """An interrupted stale executor cannot fail completed work or a replacement claim."""
    plugin = production()
    task = owned_task()
    row = dict(task, note="authoritative note", execution_state="running")
    marks = []

    async def tick(delay):
        """Advance one local scheduling cycle without real sleeps."""
        return None

    async def run(claim):
        """Race revocation/reclaim against a failing in-flight native execution."""
        if state == "reclaimed":
            row["claim_id"] = "replacement-owner"
        else:
            row["status"] = state
        plugin._stopping = True
        if failure == "cancelled":
            raise asyncio.CancelledError()
        raise RuntimeError("late native failure")

    plugin._tasks_mod = lambda: SimpleNamespace(
        due=lambda: [task], claim=lambda task_id: dict(task),
        listing=lambda **kwargs: [row], mark=lambda *args, **kwargs: marks.append(args))
    plugin._run_task_once = run
    monkeypatch.setattr(asyncio, "sleep", tick)
    asyncio.run(plugin._task_loop())
    assert marks == []
    assert row["note"] == "authoritative note" and row["execution_state"] == "running"
    assert row["claim_id"] == ("replacement-owner" if state == "reclaimed" else "owned")
    assert plugin._task_executing is False


@pytest.mark.parametrize("status", ["done", "paused", "missing", "reclaimed"])
def test_scheduler_rechecks_before_send_and_mark(production, monkeypatch, status):
    """A late no-tool final is neither delivered nor marked over authoritative storage."""
    plugin = production()
    task = owned_task()
    rows = [dict(task)]
    sends, marks = [], []

    async def tick(delay):
        """Advance one poll without wall clock sleeps."""
        return None

    async def run(claim):
        """Simulate a response racing task completion after the run entry boundary."""
        if status == "missing":
            rows.clear()
        elif status == "reclaimed":
            rows[0]["claim_id"] = "new-owner"
        else:
            rows[0]["status"] = status
        plugin._stopping = True
        return "must never be sent"

    async def send(*args, **kwargs):
        """Record forbidden final transmission without contacting QQ."""
        sends.append(args)
        return True

    plugin._tasks_mod = lambda: SimpleNamespace(
        due=lambda: [task], claim=lambda task_id: dict(task),
        listing=lambda **kwargs: rows, mark=lambda *args, **kwargs: marks.append(args))
    plugin._run_task_once = run
    plugin._send = send
    monkeypatch.setattr(asyncio, "sleep", tick)
    asyncio.run(plugin._task_loop())
    assert sends == marks == []
    assert plugin._task_executing is False
