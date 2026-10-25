"""Scheduler regression tests use isolated stores and never contact the VM."""

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import threading

import pytest


@pytest.fixture
def tasks(tmp_path, monkeypatch):
    """Load only the storage module so plugin/production dependencies stay out."""
    path = Path(__file__).resolve().parents[1] / "plugin" / "tasks.py"
    spec = importlib.util.spec_from_file_location("isolated_qqbot_tasks", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", str(tmp_path))
    monkeypatch.setattr(module, "STORE", str(tmp_path / "tasks.json"))
    monkeypatch.setattr(module, "SCRIPT_DIR", str(tmp_path))
    monkeypatch.setattr(module.time, "time", lambda: 1000.0)
    return module


def by_id(tasks):
    """Return all persisted states, including terminal tasks, by stable id."""
    return {item["id"]: item for item in tasks.listing(only_active=False)}


def test_mark_only_acknowledges_executed_one_shot(tasks):
    """One completion must not finish any other due, unexecuted one-shot."""
    first = tasks.add("first", "goal", "group", repeat="once")
    second = tasks.add("second", "goal", "group", repeat="once")
    before = by_id(tasks)[second["id"]]
    claimed = tasks.claim(first["id"])
    assert tasks.mark(first["id"], "executed", claim_id=claimed["claim_id"])
    states = by_id(tasks)
    assert states[first["id"]]["status"] == "done"
    assert states[first["id"]]["nudges"] == 1
    assert states[second["id"]] == before
    assert [t["id"] for t in tasks.due()] == [second["id"]]
    assert not tasks.mark(first["id"], "duplicate")
    assert not tasks.mark("legacy result text")
    assert by_id(tasks) == states


def test_repeated_due_schedules_only_after_completion(tasks, monkeypatch):
    """Repeated work stays active and uses completion time, without catch-up."""
    task = tasks.add("repeat", "goal", "group", interval_s=60)
    claimed = tasks.claim(task["id"])
    assert tasks.due(now=5000) == []
    monkeypatch.setattr(tasks.time, "time", lambda: 1100.0)
    assert tasks.mark(task["id"], "ok", claim_id=claimed["claim_id"])
    state = by_id(tasks)[task["id"]]
    assert state["status"] == "active"
    assert state["next_run_at"] == 1160
    assert tasks.due(now=1159) == []
    assert len(tasks.due(now=1160)) == 1
    claimed = tasks.claim(task["id"], now=1160)
    assert claimed is not None
    assert tasks.mark(task["id"], "again", claim_id=claimed["claim_id"])
    assert by_id(tasks)[task["id"]]["nudges"] == 2


def test_claim_is_single_process_exclusive_and_persistent(tasks):
    """Concurrent pollers and later reloads cannot replay one running claim."""
    task = tasks.add("race", "goal", "group")
    barrier = threading.Barrier(8)

    def compete():
        """Synchronize contenders to exercise the full read/write transaction."""
        barrier.wait()
        return tasks.claim(task["id"])

    with ThreadPoolExecutor(max_workers=8) as pool:
        claimed = list(pool.map(lambda _: compete(), range(8)))
    assert sum(result is not None for result in claimed) == 1
    with open(tasks.STORE, encoding="utf-8") as handle:
        persisted = json.load(handle)[0]
    assert persisted["execution_state"] == "running"
    assert persisted["claim_id"]
    assert tasks.claim(task["id"], now=999999) is None
    assert tasks.due(now=999999) == []


def test_failure_pauses_without_finishing_or_blind_retry(tasks):
    """Unknown tool outcomes require explicit authorization before retry."""
    task = tasks.add("failure", "goal", "group", repeat="once")
    claimed = tasks.claim(task["id"])
    assert tasks.mark(task["id"], "uncertain outcome", success=False,
                      claim_id=claimed["claim_id"])
    state = by_id(tasks)[task["id"]]
    assert state["status"] == "paused"
    assert state["execution_state"] == "failed"
    assert "finished_at" not in state
    assert tasks.due(now=999999) == []
    tasks.set_status(task_id=task["id"], status="active")
    retried = tasks.claim(task["id"])
    assert not tasks.mark(task["id"], claim_id=claimed["claim_id"])
    assert tasks.mark(task["id"], claim_id=retried["claim_id"])


def test_pause_cancel_query_and_in_task_completion(tasks):
    """Status queries include paused work, and completion never reopens it."""
    first = tasks.add("first", "goal", "one")
    other = tasks.add("other", "goal", "two")
    tasks.set_status(task_id=first["id"], title="other", status="paused")
    assert by_id(tasks)[other["id"]]["status"] == "active"
    assert tasks.listing("one", status="paused")[0]["id"] == first["id"]
    tasks.set_status(task_id=first["id"], status="cancelled")
    assert tasks.listing("one", status="cancelled")
    assert tasks.claim(first["id"]) is None
    claimed = tasks.claim(other["id"])
    tasks.set_status(task_id=other["id"], status="done")
    assert tasks.mark(other["id"], claim_id=claimed["claim_id"])
    assert by_id(tasks)[other["id"]]["status"] == "done"
    with pytest.raises(ValueError):
        tasks.set_status(task_id=other["id"], status="typo")


def test_add_upsert_updates_fields_without_resuming_paused(tasks):
    """Upsert persists trigger/context and matches normalized stored titles."""
    task = tasks.add("x" * 100, "old", "group", origin="old-origin")
    tasks.set_status(task_id=task["id"], status="paused")
    updated = tasks.add("x" * 100, "new", "group", interval_s=5,
                        repeat="once", delay_s=40, brief="brief",
                        resume_prompt="continue", trigger_kind="script",
                        trigger_script="print(True)", trigger_every_s=35,
                        origin="new-origin")
    assert updated["id"] == task["id"]
    assert len(tasks.listing(only_active=False)) == 1
    assert updated["status"] == "paused"
    assert updated["goal"] == "new"
    assert updated["brief"] == "brief"
    assert updated["resume_prompt"] == "continue"
    assert updated["origin"] == "new-origin"
    assert updated["trigger_kind"] == "script"
    assert updated["trigger_script"] == "print(True)"
    assert updated["trigger_every_s"] == 35
    assert updated["interval_s"] == tasks.MIN_INTERVAL
    assert updated["next_run_at"] == 1040


def test_update_keeps_resume_prompt_and_running_claim(tasks):
    """Model updates change future work without invalidating execution owner."""
    task = tasks.add("task", "goal", "group", resume_prompt="old")
    claimed = tasks.claim(task["id"])
    tasks.update(task_id=task["id"], resume_prompt="new", interval_s=45,
                 origin="origin", repeat="once", delay_s=100, goal="new goal")
    state = by_id(tasks)[task["id"]]
    assert state["resume_prompt"] == "new"
    assert state["claim_id"] == claimed["claim_id"]
    assert state["next_run_at"] == 1000
    assert state["origin"] == "origin"
    assert tasks.mark(task["id"], claim_id=claimed["claim_id"])
    assert by_id(tasks)[task["id"]]["status"] == "done"
    assert by_id(tasks)[task["id"]]["resume_prompt"] == "new"


def test_script_false_reschedules_without_execution(tasks, monkeypatch):
    """Polling is side-effect free and false conditions only reschedule probe."""
    task = tasks.add("script", "goal", "group", repeat="once",
                     trigger_kind="script", trigger_script="predicate")
    calls = []

    def condition(code):
        """Record condition evaluation without executing any external code."""
        calls.append(code)
        return False, "false"

    monkeypatch.setattr(tasks, "run_trigger_script", condition)
    assert tasks.due() and tasks.due()
    assert calls == []
    assert tasks.claim(task["id"]) is None
    state = by_id(tasks)[task["id"]]
    assert state["status"] == "active"
    assert state["nudges"] == 0
    assert state["next_run_at"] == 1060
    assert calls == ["predicate"]
    assert not tasks.mark(task["id"])


def test_script_failure_does_not_retry_unknown_effects(tasks, monkeypatch):
    """A timeout could have mutated state, so unlike false it needs review."""
    task = tasks.add("script", "goal", "group", trigger_kind="script",
                     trigger_script="side_effect()")
    monkeypatch.setattr(tasks, "run_trigger_script", lambda code: (False, "timeout"))
    assert tasks.claim(task["id"]) is None
    assert by_id(tasks)[task["id"]]["status"] == "paused"
    assert tasks.due(now=99999) == []


def test_schedule_next_false_pauses_repeated_task(tasks):
    """Honor the old scheduling switch without leaving a busy due task."""
    task = tasks.add("repeat", "goal", "group")
    claimed = tasks.claim(task["id"])
    assert tasks.mark(task["id"], schedule_next=False, claim_id=claimed["claim_id"])
    assert by_id(tasks)[task["id"]]["status"] == "paused"
    assert tasks.due() == []


def test_successful_trigger_is_claimed_only_once(tasks, monkeypatch):
    """A true condition returns an already durable exclusive execution claim."""
    task = tasks.add("script", "goal", "group", trigger_kind="script",
                     trigger_script="predicate")
    calls = []

    def condition(code):
        """Verify persistence precedes condition execution, even on success."""
        calls.append(code)
        assert by_id(tasks)[task["id"]]["execution_state"] == "running"
        return True, "ready"

    monkeypatch.setattr(tasks, "run_trigger_script", condition)
    claimed = tasks.claim(task["id"])
    assert claimed["trigger_last"] == "ready"
    assert tasks.claim(task["id"]) is None
    assert calls == ["predicate"]
    assert tasks.mark(task["id"], claim_id=claimed["claim_id"])


def test_persistence_failure_does_not_execute_trigger(tasks, monkeypatch):
    """A claim must reach disk before any script or downstream tool can run."""
    task = tasks.add("script", "goal", "group", trigger_kind="script",
                     trigger_script="predicate")
    calls = []

    def cannot_save(items):
        """Simulate disk failure before a claim can become durable."""
        raise OSError("disk unavailable")

    monkeypatch.setattr(tasks, "_save", cannot_save)
    monkeypatch.setattr(tasks, "run_trigger_script", lambda code: calls.append(code))
    with pytest.raises(OSError, match="disk unavailable"):
        tasks.claim(task["id"])
    assert calls == []
    assert by_id(tasks)[task["id"]]["execution_state"] == "idle"


def test_terminal_running_claim_survives_capacity_cleanup(tasks, monkeypatch):
    """In-flight cancellation cannot evict its still-unacknowledged claim."""
    monkeypatch.setattr(tasks, "MAX_TASKS", 1)
    task = tasks.add("running", "goal", "group")
    tasks.claim(task["id"])
    tasks.set_status(task_id=task["id"], status="cancelled")
    with pytest.raises(ValueError):
        tasks.add("new", "goal", "group")
    assert by_id(tasks)[task["id"]]["execution_state"] == "running"


def test_corrupt_store_is_not_silently_replaced(tasks):
    """Corruption must preserve evidence rather than erase execution claims."""
    Path(tasks.STORE).write_text("{broken", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        tasks.add("new", "goal", "group")
    assert Path(tasks.STORE).read_text(encoding="utf-8") == "{broken"


def test_task_limit_preserves_paused_and_claimed_work(tasks, monkeypatch):
    """Capacity cleanup cannot drop paused/running tasks and lose ownership."""
    monkeypatch.setattr(tasks, "MAX_TASKS", 2)
    first = tasks.add("first", "goal", "group")
    second = tasks.add("second", "goal", "group")
    tasks.set_status(task_id=first["id"], status="paused")
    tasks.claim(second["id"])
    with pytest.raises(ValueError):
        tasks.add("third", "goal", "group")
    assert set(by_id(tasks)) == {first["id"], second["id"]}
