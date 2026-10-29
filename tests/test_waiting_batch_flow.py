"""Sequential platform ingress must not await owned model/tool/send queue workers.

The production entry and worker are AST-loaded, with no AstrBot startup or network.
Events, not elapsed sleeps or a batching window, define every contested boundary.
"""

import ast
import asyncio
import copy
import json
from types import MethodType

import pytest

import test_production_integration as integration
from test_batch_flow import _entry, _envelope, _final, _sent

production = integration.production


def _waiting_entry(plugin):
    """Bind real ingress/worker methods even when older fixtures wrap entry completion."""
    source = integration.ROOT / "plugin" / "main.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    klass = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                 and node.name == "HiganPlugin")
    names = {"on_message", "_drain_pending", "_start_background"}
    methods = [copy.deepcopy(node) for node in klass.body
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    assert {method.name for method in methods} == names
    for method in methods:
        # Registration decorators are framework I/O; preserve every method body.
        method.decorator_list = []
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[
        ast.alias(name="annotations")], level=0), *methods], type_ignores=[])
    namespace = type(plugin)._handle_one.__globals__.copy()
    exec(compile(ast.fix_missing_locations(module), str(source), "exec"), namespace)
    for name in names:
        setattr(plugin, name, MethodType(namespace[name], plugin))
    plugin._background_tasks = set()
    _entry(plugin)
    return plugin


async def _settle(plugin):
    """Await all owned finite workers without cancelling an active tool or using sleeps."""
    while plugin._background_tasks:
        await asyncio.wait_for(asyncio.gather(*tuple(plugin._background_tasks)), timeout=2)


@pytest.mark.parametrize("answer", ["All pending subjects together.", "SILENT"])
def test_sequential_platform_ingress_batches_every_arrival_until_lock_acquired(production, monkeypatch, answer):
    """Three serial platform callbacks return while a previous task still owns execution."""
    plugin = _waiting_entry(production())
    events = [integration.Event("subject %s" % index, str(700 + index)) for index in range(3)]
    final = answer if answer == "SILENT" else _final(
        answer, *(event.message_obj.message_id for event in events))
    requests = integration._provider(monkeypatch, [(final, [])], auto_wrap=False)
    sleeps = []

    async def forbidden_sleep(delay, *args, **kwargs):
        """Make any fixed debounce fail instead of letting a timing-dependent test pass."""
        sleeps.append(delay)
        raise AssertionError("batching must not sleep")

    async def scenario():
        """Use the real lock acquisition boundary to admit serial callbacks without time windows."""
        waiting = asyncio.Event()

        class ObservedLock(asyncio.Lock):
            """Expose the worker joining asyncio's real FIFO waiter queue."""

            async def acquire(self):
                """Signal contention without changing lock ownership or scheduling order."""
                if self.locked():
                    waiting.set()
                return await super().acquire()

        plugin._execution_lock = ObservedLock()
        await plugin._execution_lock.acquire()
        try:
            # This is the framework's serial await shape, not concurrent task injection.
            await asyncio.wait_for(plugin.on_message(events[0]), timeout=2)
            await asyncio.wait_for(waiting.wait(), timeout=2)
            await asyncio.wait_for(plugin.on_message(events[1]), timeout=2)
            await asyncio.wait_for(plugin.on_message(events[2]), timeout=2)
            assert len(plugin._pending["42"]) == 3
            assert len(plugin._background_tasks) == 1 and plugin._busy["42"]
            assert requests == [] and _sent(events) == []
            assert all(event.stopped for event in events)
        finally:
            plugin._execution_lock.release()
            await _settle(plugin)

    monkeypatch.setattr(asyncio, "sleep", forbidden_sleep)
    asyncio.run(scenario())
    assert sleeps == []
    assert len(requests) == 1 and len(_sent(events)) == (0 if answer == "SILENT" else 1)
    assert [record["id"] for record in _envelope(requests[0])["messages"]] == ["700", "701", "702"]
    assert not any(plugin._pending.values()) and plugin._turn_context == {}
    assert plugin._busy == {"42": False} and not plugin._background_tasks


def test_serial_arrivals_during_native_tool_join_next_turn_without_cancellation(production):
    """A running tool is immutable; later serial callbacks queue once for the next decision."""
    plugin = _waiting_entry(production())
    events = [integration.Event("subject %s" % index, str(710 + index)) for index in range(3)]
    requests, effects = [], []

    async def scenario():
        """Block only the native tool I/O while retaining the actual continuation and send paths."""
        started, release = asyncio.Event(), asyncio.Event()

        async def model(system, prompt, refs, messages=None, allow_tools=True):
            """Capture real native requests and answer each consumed batch exactly once."""
            requests.append(copy.deepcopy(messages))
            if len(requests) == 1:
                return "", [integration._call("self_status", call_id="waiting-native-tool")]
            if len(requests) == 2:
                assert messages[-1]["role"] == "tool"
                assert messages[-1]["tool_call_id"] == "waiting-native-tool"
                return _final("First task finished.", events[0].message_obj.message_id), []
            assert len(requests) == 3, "Consumed messages must not be replayed"
            return _final("Both later subjects considered together.",
                          events[1].message_obj.message_id, events[2].message_obj.message_id), []

        async def tool(name, args, group="", event=None):
            """Track completion and make cancellation an observable regression."""
            effects.append("started")
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                effects.append("cancelled")
                raise
            effects.append("completed")
            return {"ok": True, "source": "waiting-native-tool"}

        plugin._llm_tools, plugin._dispatch_tool = model, tool
        await asyncio.wait_for(plugin.on_message(events[0]), timeout=2)
        await asyncio.wait_for(started.wait(), timeout=2)
        try:
            await asyncio.wait_for(plugin.on_message(events[1]), timeout=2)
            await asyncio.wait_for(plugin.on_message(events[2]), timeout=2)
            assert effects == ["started"] and len(requests) == 1
            assert [record["id"] for record in plugin._turn_context["42"]["messages"]] == ["710"]
            assert len(plugin._pending["42"]) == 2
        finally:
            release.set()
            await _settle(plugin)

    asyncio.run(scenario())
    assert effects == ["started", "completed"] and len(requests) == 3
    first = json.loads(requests[0][1]["content"])["conversation"]["messages"]
    following = json.loads(requests[2][1]["content"])["conversation"]["messages"]
    assert [record["id"] for record in first] == ["710"]
    assert [record["id"] for record in following] == ["711", "712"]
    assert len(_sent(events)) == 2 and not any(plugin._pending.values())
    assert plugin._turn_context == {} and not plugin._background_tasks


def test_scope_workers_keep_shared_lock_fifo_and_never_steal_other_scope(production):
    """A waiting scope coalesces its arrivals without overtaking a task or another scope."""
    plugin, turns = _waiting_entry(production()), []
    first = integration.Event("first scope", "720", group="42")
    other = integration.Event("other scope", "721", group="43")
    later = integration.Event("same first scope", "722", group="42")

    async def scenario():
        """Observe each waiter arriving, then release the existing owner once all are queued."""
        waited = {name: asyncio.Event() for name in ("42", "task", "43")}
        queued_names = iter(("42", "task", "43"))

        class ObservedLock(asyncio.Lock):
            """Retain the native FIFO queue while exposing deterministic contention events."""

            async def acquire(self):
                """Signal each known waiter exactly when it joins the locked queue."""
                if self.locked():
                    waited[next(queued_names)].set()
                return await super().acquire()

        async def handle(event, group, text, has_image, woken):
            """Inspect the real collection result at its policy-safe processing anchor."""
            turns.append((group, [record["id"] for record in json.loads(text)["messages"]]))

        async def due_task():
            """Represent independent work using the very same execution-lock FIFO queue."""
            async with plugin._execution_lock:
                turns.append(("task", []))

        plugin._handle_one = handle
        plugin._execution_lock = ObservedLock()
        await plugin._execution_lock.acquire()
        task = None
        try:
            await plugin.on_message(first)
            await asyncio.wait_for(waited["42"].wait(), timeout=2)
            task = asyncio.create_task(due_task())
            await asyncio.wait_for(waited["task"].wait(), timeout=2)
            await plugin.on_message(other)
            await asyncio.wait_for(waited["43"].wait(), timeout=2)
            await plugin.on_message(later)
            assert len(plugin._background_tasks) == 2 and turns == []
        finally:
            plugin._execution_lock.release()
            await _settle(plugin)
            if task is not None:
                await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())
    assert turns == [("42", ["720", "722"]), ("task", []), ("43", ["721"])]
    assert all(event.stopped for event in (first, other, later))
    assert not any(plugin._pending.values()) and not any(plugin._busy.values())


@pytest.mark.parametrize("revoke_all", [False, True])
def test_waiting_batch_rechecks_permissions_at_dequeue(production, monkeypatch, revoke_all):
    """A policy change while waiting removes only denied entries before model/send selection."""
    plugin = _waiting_entry(production())
    events = [integration.Event("allowed at ingress", "730"),
              integration.Event("latest admitted", "731")]
    denied = set()
    original = plugin._policy_allows
    plugin._policy_allows = lambda event, group: event.message_obj.message_id not in denied and original(event, group)
    requests = integration._provider(monkeypatch, [] if revoke_all else [
        (_final("Only still permitted input.", events[1].message_obj.message_id), [])
    ], auto_wrap=False)

    async def scenario():
        """Change admission policy only after both serial callbacks have enqueued their data."""
        plugin._execution_lock = asyncio.Lock()
        await plugin._execution_lock.acquire()
        try:
            for event in events:
                await asyncio.wait_for(plugin.on_message(event), timeout=2)
            assert len(plugin._pending["42"]) == 2
            denied.add("730")
            if revoke_all:
                denied.add("731")
        finally:
            plugin._execution_lock.release()
            await _settle(plugin)

    asyncio.run(scenario())
    assert len(requests) == len(_sent(events)) == (0 if revoke_all else 1)
    if not revoke_all:
        assert [record["id"] for record in _envelope(requests[0])["messages"]] == ["731"]
        assert events[0].sent == [] and len(events[1].sent) == 1
    assert not any(plugin._pending.values()) and not any(plugin._busy.values())
    assert plugin._turn_context == {} and not plugin._background_tasks


def test_serial_arrivals_during_confirmed_send_form_one_followup(production, monkeypatch):
    """A queued worker owns the awaited send boundary as well as model and tool execution."""
    plugin = _waiting_entry(production())
    events = [integration.Event("send subject %s" % index, str(740 + index)) for index in range(3)]
    requests = integration._provider(monkeypatch, [
        (_final("First reply.", events[0].message_obj.message_id), []),
        (_final("Combined followup.", events[1].message_obj.message_id,
                events[2].message_obj.message_id), [])], auto_wrap=False)

    async def scenario():
        """Withhold the first send confirmation while two sequential inputs return normally."""
        started, release = asyncio.Event(), asyncio.Event()
        original_send = events[0].send

        async def send(chain):
            """Preserve actual delivery bookkeeping after an explicit confirmation event."""
            started.set()
            await release.wait()
            return await original_send(chain)

        events[0].send = send
        await asyncio.wait_for(plugin.on_message(events[0]), timeout=2)
        await asyncio.wait_for(started.wait(), timeout=2)
        try:
            await asyncio.wait_for(plugin.on_message(events[1]), timeout=2)
            await asyncio.wait_for(plugin.on_message(events[2]), timeout=2)
            assert len(requests) == 1 and _sent(events) == []
            assert len(plugin._pending["42"]) == 2 and plugin._busy["42"]
        finally:
            release.set()
            await _settle(plugin)

    asyncio.run(scenario())
    assert len(requests) == len(_sent(events)) == 2
    assert [record["id"] for record in _envelope(requests[0])["messages"]] == ["740"]
    assert [record["id"] for record in _envelope(requests[1])["messages"]] == ["741", "742"]
    assert not any(plugin._pending.values()) and plugin._turn_context == {}


def test_reload_waits_owned_worker_and_copies_only_confirmed_delivery(production, monkeypatch):
    """Returning ingress cannot hide active send ownership from replacement instance draining."""
    old, new = _waiting_entry(production()), production()
    event = integration.Event("delivery pending during reload", "750")
    integration._provider(monkeypatch, [
        (_final("Confirmed before replacement.", event.message_obj.message_id), [])
    ], auto_wrap=False)

    async def scenario():
        """Use drain's existing polling boundary as a handshake rather than elapsed time."""
        started, release = asyncio.Event(), asyncio.Event()
        observed, next_scan = asyncio.Event(), asyncio.Event()
        original_send = event.send

        async def send(chain):
            """Hold delivery without suppressing the production worker's await chain."""
            started.set()
            await release.wait()
            return await original_send(chain)

        async def polling_boundary(delay):
            """Only reload polling is allowed; test progress is controlled by events."""
            assert delay == 0.05
            observed.set()
            await next_scan.wait()

        event.send = send
        await old.on_message(event)
        await asyncio.wait_for(started.wait(), timeout=2)
        assert old._busy["42"] and len(old._background_tasks) == 1
        monkeypatch.setattr(asyncio, "sleep", polling_boundary)
        drain = asyncio.create_task(new._drain_previous_instances())
        try:
            await asyncio.wait_for(observed.wait(), timeout=2)
            assert not drain.done() and old._stopping
            assert event.sent == [] and new._last_sent == {}
            assert not any(task.cancelled() for task in old._background_tasks)
            release.set()
            await _settle(old)
            next_scan.set()
            await asyncio.wait_for(drain, timeout=2)
        finally:
            release.set()
            next_scan.set()
            await _settle(old)
            if not drain.done():
                await asyncio.wait_for(drain, timeout=2)
        assert len(event.sent) == 1
        assert new._last_sent == old._last_sent and new._last_sent
        assert new._recent == old._recent
        assert not old._busy["42"] and not old._background_tasks

    asyncio.run(scenario())
