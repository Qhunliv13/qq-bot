"""Offline FIFO fairness contracts for the shared native task/chat execution lock."""

import asyncio
from types import SimpleNamespace

import test_production_integration as integration

production = integration.production


def test_due_task_waits_without_preemption_then_precedes_later_chat(production, monkeypatch):
    """A due task joins the same FIFO queue once, rather than polling past a busy session."""
    plugin, trace = production(), []
    task = {"id": "due", "scope": "42", "origin": "offline:42", "claim_id": "owned"}

    async def scenario():
        """Coordinate actual lock contention with events and bound every potentially blocking wait."""
        task_waiting, chat_waiting = asyncio.Event(), asyncio.Event()
        background = None

        class ObservedLock(asyncio.Lock):
            """Identify waiter arrivals without changing asyncio.Lock's FIFO semantics."""

            async def acquire(self):
                """Expose the scheduler and later foreground waiter before delegating acquisition."""
                if self.locked():
                    if asyncio.current_task() is background:
                        task_waiting.set()
                    else:
                        chat_waiting.set()
                return await super().acquire()

        def due():
            """Return the single persisted due candidate without running any action."""
            trace.append("due")
            return [task]

        def claim(task_id):
            """A task can only be claimed after the original foreground owner releases."""
            assert plugin._execution_lock.locked()
            assert task_id == "due"
            trace.append("claim")
            return dict(task)

        async def run(owned):
            """Represent serialized native continuation with no model or VM call."""
            assert plugin._execution_lock.locked() and owned["claim_id"] == "owned"
            trace.append("run")
            return ""

        def mark(task_id, result, **kwargs):
            """Finish the test after one acknowledgement while retaining native scheduler control flow."""
            assert task_id == "due" and kwargs["success"] is True
            trace.append("mark")
            plugin._stopping = True
            return True

        async def tick(delay):
            """Advance polling immediately; synchronization never depends on elapsed sleep time."""
            assert delay == 25

        async def later_chat():
            """A later foreground turn must not jump ahead of an already queued due task."""
            async with plugin._execution_lock:
                trace.append("later_chat")

        plugin._execution_lock = ObservedLock()
        plugin._pending["42"] = [("continuous pending chat",)]
        plugin._tasks_mod = lambda: SimpleNamespace(
            due=due, claim=claim, mark=mark,
            listing=lambda **kwargs: [{**task, "status": "active"}])
        plugin._run_task_once = run
        monkeypatch.setattr(asyncio, "sleep", tick)
        await plugin._execution_lock.acquire()
        trace.append("foreground_running")
        background = asyncio.create_task(plugin._task_loop())
        chatter = None
        try:
            await asyncio.wait_for(task_waiting.wait(), timeout=2)
            assert trace == ["foreground_running", "due"]
            chatter = asyncio.create_task(later_chat())
            await asyncio.wait_for(chat_waiting.wait(), timeout=2)
            assert trace == ["foreground_running", "due"]
            trace.append("foreground_released")
            plugin._execution_lock.release()
            await asyncio.wait_for(asyncio.gather(background, chatter), timeout=2)
        finally:
            remaining = [handle for handle in (background, chatter) if handle is not None and not handle.done()]
            for handle in remaining:
                handle.cancel()
            if remaining:
                await asyncio.gather(*remaining, return_exceptions=True)
            if plugin._execution_lock.locked():
                plugin._execution_lock.release()

    asyncio.run(scenario())
    assert trace == ["foreground_running", "due", "foreground_released", "claim", "run", "mark", "later_chat"]


def test_native_shutdown_while_scheduler_waits_never_claims_new_work(production, monkeypatch):
    """Recheck shutdown under the FIFO lock, not only before the polling candidate loop."""
    plugin = production()
    claimed = []

    async def scenario():
        """Hold the actual execution lock until stop closes new task admission."""
        waiting = asyncio.Event()

        class ObservedLock(asyncio.Lock):
            """Expose the scheduler reaching the blocked acquisition."""
            async def acquire(self):
                """Notify only contention, preserving the native asyncio lock implementation."""
                if self.locked():
                    waiting.set()
                return await super().acquire()

        async def tick(_delay):
            """Avoid a real twenty-five second polling delay in this isolated test."""
            return None

        plugin._execution_lock = ObservedLock()
        plugin._tasks_mod = lambda: SimpleNamespace(
            due=lambda: [{"id": "pending", "origin": "offline:42"}],
            claim=lambda ident: claimed.append(ident))
        monkeypatch.setattr(asyncio, "sleep", tick)
        await plugin._execution_lock.acquire()
        task = asyncio.create_task(plugin._task_loop())
        try:
            await asyncio.wait_for(waiting.wait(), 2)
            plugin._stopping = True
            plugin._execution_lock.release()
            await asyncio.wait_for(task, 2)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
    assert claimed == []
