"""Offline tests drive actual concurrent asyncio workers, never network/model mocks."""

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugin import delegation  # noqa: E402


def test_schema_exact_contract():
    """Keep the parent-facing native tool names and required fields stable."""
    tools = {tool["function"]["name"]: tool["function"]["parameters"] for tool in delegation.schema()}
    assert set(tools) == {"agent_start", "agent_status", "agent_list", "agent_message", "agent_interrupt"}
    assert tools["agent_start"]["required"] == ["task"]
    assert tools["agent_message"]["required"] == ["id", "message"]
    assert tools["agent_list"]["properties"] == {}


def test_concurrent_workers_messages_and_receipts(tmp_path):
    """Two actual workers overlap; instructions wait for explicit inbox polling."""
    async def scenario():
        """Control both real workers with events rather than sleeps or fake tasks."""
        manager = delegation.Manager(tmp_path)
        started = {name: asyncio.Event() for name in ("one", "two")}
        release = asyncio.Event()
        received = {}

        async def runner(task, scope, inbox):
            """Wait concurrently then drain input at the native model-call boundary."""
            assert scope == "group-a"
            started[task].set()
            await release.wait()
            received[task] = await inbox()
            assert await inbox() == []
            return task + " result"

        one = await manager.start("one", "first", runner, scope="group-a")
        two = await manager.start("two", None, runner, scope="group-a")
        await asyncio.gather(*(event.wait() for event in started.values()))
        assert one["id"] != two["id"]
        assert all(item["status"] == "running" for item in manager.list(scope="group-a")["agents"])
        page = manager.list(scope="group-a", limit=1)
        assert len(page["agents"]) == 1 and page["next_offset"] == 1
        assert "result" not in page["agents"][0]
        assert len(manager.list(scope="group-a", offset=page["next_offset"])["agents"]) == 1
        assert manager.message(one["id"], "next instruction", scope="group-a")["status"] == "queued"
        assert received == {}
        live_manager = delegation.Manager(tmp_path / "." )
        assert live_manager._jobs is manager._jobs
        assert live_manager.status(one["id"], scope="group-a")["attached"] is True
        release.set()
        await asyncio.gather(*(job["worker"] for job in manager._jobs.values()))
        assert received == {"one": ["next instruction"], "two": []}
        restored = delegation.Manager(tmp_path)
        assert restored._jobs is manager._jobs
        assert restored.status(one["id"], scope="group-a")["result"] == "one result"
        assert restored.message(one["id"], "must not resume", scope="group-a")["status"] == "not_running"
        assert restored.interrupt(one["id"], scope="group-a")["status"] == "not_running"
        records = [json.loads(path.read_text(encoding="utf-8")) for path in tmp_path.glob("*.json")]
        assert len(records) == 2
        assert all(record["status"] == "completed" for record in records)
        assert not list(tmp_path.glob(".delegation-*"))

    asyncio.run(scenario())


def test_cancel_only_target_and_prestart_cancellation(tmp_path):
    """Cancellation targets one owned task and also captures pre-start cancellation."""
    async def scenario():
        """Prove parent and sibling survive an individual worker interruption."""
        manager = delegation.Manager(tmp_path)
        release = asyncio.Event()
        entered = asyncio.Event()

        async def runner(task, scope, inbox):
            """Keep workers live until the explicit test-controlled release."""
            entered.set()
            await release.wait()
            return task

        one = await manager.start("cancel", None, runner, scope="a")
        two = await manager.start("survive", None, runner, scope="a")
        await entered.wait()
        assert manager.interrupt(one["id"], scope="a")["status"] == "cancelling"
        await manager._jobs[one["id"]]["worker"]
        assert manager.status(one["id"], scope="a")["status"] == "cancelled"
        assert not manager._jobs[two["id"]]["worker"].done()
        assert not asyncio.current_task().cancelled()
        release.set()
        await manager._jobs[two["id"]]["worker"]
        early = await manager.start("early", None, runner, scope="a")
        manager.interrupt(early["id"], scope="a")
        await asyncio.gather(manager._jobs[early["id"]]["worker"], return_exceptions=True)
        assert manager.status(early["id"], scope="a")["status"] == "cancelled"

    asyncio.run(scenario())


def test_scope_isolation_and_invalid_ids(tmp_path):
    """Other scopes and path-like identifiers cannot inspect or affect workers."""
    async def scenario():
        """Exercise every manager entry point against a foreign scope."""
        manager = delegation.Manager(tmp_path)
        release = asyncio.Event()

        async def runner(task, scope, inbox):
            """Provide a live worker to verify denied cancellation has no effect."""
            await release.wait()
            return "private result"

        receipt = await manager.start("private task", None, runner, scope="private")
        id = receipt["id"]
        assert manager.status(id, scope="other")["error"] == "not_found"
        assert manager.list(scope="other")["agents"] == []
        assert manager.message(id, "bad", scope="other")["error"] == "not_found"
        assert manager.interrupt(id, scope="other")["error"] == "not_found"
        for invalid in ("../../outside", "", "not-a-uuid", None, 42):
            assert manager.status(invalid, scope="private")["status"] == "unknown"
        assert not manager._jobs[id]["worker"].cancelled()
        release.set()
        await manager._jobs[id]["worker"]

    asyncio.run(scenario())


def test_redaction_error_and_output_pagination(tmp_path):
    """Receipts redact secrets, bound text, and never expose exception messages."""
    async def scenario():
        """Exercise successful and failed terminal snapshots without network calls."""
        manager = delegation.Manager(tmp_path)
        secret = "sk-" + "x" * 40

        async def success(task, scope, inbox):
            """Return a long secret-bearing result to verify centralized sanitation."""
            return secret + " " + "雪" * 11000

        async def failure(task, scope, inbox):
            """Throw an upstream-style error whose text must never be persisted."""
            raise ValueError("password=do-not-persist")

        good = await manager.start(secret, secret, success, scope="s")
        bad = await manager.start("failure", None, failure, scope="s")
        await asyncio.gather(*(job["worker"] for job in manager._jobs.values()))
        output = manager.status(good["id"], scope="s", offset=0, limit=5)
        assert len(output["result"]) == 5 and output["next_offset"] == 5
        assert manager.status(good["id"], scope="s")["result_truncated"] is True
        assert manager.status(bad["id"], scope="s")["error"] == "ValueError"
        assert manager.status(bad["id"], scope="s")["status"] == "failed"
        for path in tmp_path.glob("*.json"):
            content = path.read_text(encoding="utf-8")
            assert secret not in content and "do-not-persist" not in content
            if os.name != "nt":
                assert stat.S_IMODE(path.stat().st_mode) == 0o600
        with pytest.raises(ValueError):
            manager.status(good["id"], scope="s", limit=10001)
        with pytest.raises(ValueError):
            manager.list(scope="s", offset=-1)

    asyncio.run(scenario())


def test_restart_orphan_does_not_rerun_or_rewrite(tmp_path):
    """A missing process yields unknown without modifying the persisted receipt."""
    async def scenario():
        """Write an orphan fixture by modifying only a completed test receipt."""
        manager = delegation.Manager(tmp_path)

        async def runner(task, scope, inbox):
            """Complete normally before constructing the simulated orphan record."""
            return "done"

        receipt = await manager.start("task", None, runner, scope="s")
        await manager._jobs[receipt["id"]]["worker"]
        path = tmp_path / (receipt["id"] + ".json")
        record = json.loads(path.read_text(encoding="utf-8"))
        record.update(status="running", owner_pid=-1, boot_id="previous-process")
        path.write_text(json.dumps(record), encoding="utf-8")
        manager._jobs.clear()
        before = path.read_bytes()
        restored = delegation.Manager(tmp_path)
        assert restored.status(receipt["id"], scope="s")["status"] == "unknown"
        assert restored.interrupt(receipt["id"], scope="s")["status"] == "unattached"
        assert path.read_bytes() == before
        assert restored._jobs == {}

    asyncio.run(scenario())


def test_namespace_reload_unattached_worker_keeps_finishing(tmp_path):
    """Fresh module namespaces cannot steal tasks but can observe their receipts."""
    async def scenario():
        """Keep the original actual worker alive through a separate module import."""
        manager = delegation.Manager(tmp_path)
        release = asyncio.Event()

        async def runner(task, scope, inbox):
            """Wait until the unattached observer has verified safe behavior."""
            await release.wait()
            return "finished after reload"

        receipt = await manager.start("task", None, runner, scope="s")
        spec = importlib.util.spec_from_file_location("plugin._delegation_reload_test", delegation.__file__)
        other_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(other_module)
        observer = other_module.Manager(tmp_path)
        status = observer.status(receipt["id"], scope="s")
        assert status["status"] == "running" and status["ownership"] == "running_unattached"
        assert status["attached"] is False
        assert observer.interrupt(receipt["id"], scope="s")["status"] == "unattached"
        assert observer.message(receipt["id"], "ignored", scope="s")["status"] == "unattached"
        release.set()
        await manager._jobs[receipt["id"]]["worker"]
        assert observer.status(receipt["id"], scope="s")["result"] == "finished after reload"

    asyncio.run(scenario())
