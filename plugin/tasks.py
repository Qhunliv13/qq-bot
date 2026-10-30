#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""后台任务的持久存储、排程、认领和结果记录。

存储与执行约束：
  · 单一存储：配置的数据根目录下 tasks/tasks.json（持久化于执行环境）
  · 任务字段：id / title / goal / scope（群号或 private:QQ）/ interval_s / repeat /
              status（active|paused|done|cancelled）/ next_run_at / nudges / origin
  · `due()` 只列候选；执行前必须 `claim(id)`，执行后 `mark(id, result, ...)`。
  · `repeat="once"` 仅执行成功后 done；循环默认从完成时间重排，fixed_rate 显式保持原定相位。
  · 失败暂停，运行中的认领永久保留，避免不确定副作用被自动重试（线程安全，POSIX 进程间加锁）。
"""

from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
import json
import math
from numbers import Real
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid

# Threads share a reentrant lock; POSIX processes additionally coordinate on a
# stable sidecar inode, since atomic replacement changes the JSON file's inode.
_LOCK = threading.RLock()
_TRANSACTION_LOCAL = threading.local()


@contextmanager
def _file_transaction_lock():
    """Hold a per-store POSIX advisory lock, reusing nested ownership in this thread.

    Windows uses thread-only locking. All cooperating POSIX
    writers must call the public decorated API rather than _save directly.
    """
    if os.name != "posix":
        yield
        return
    import fcntl

    path = os.path.abspath(STORE) + ".lock"
    owned = getattr(_TRANSACTION_LOCAL, "paths", None)
    if owned is None:
        owned = _TRANSACTION_LOCAL.paths = set()
    if path in owned:
        # Reopening/flocking a second descriptor could deadlock a nested call.
        yield
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        owned.add(path)
        try:
            yield
        finally:
            owned.remove(path)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _locked(function):
    """Serialize the entire read/modify/write transaction across threads and POSIX processes."""
    @wraps(function)
    def wrapper(*args, **kwargs):
        with _LOCK:
            with _file_transaction_lock():
                return function(*args, **kwargs)
    return wrapper

# AstrBot imports a package; standalone offline probes import this file directly.
if __package__:
    from .runtime_paths import data_root
else:
    from plugin.runtime_paths import data_root

ROOT = str(data_root() / "tasks")
STORE = os.path.join(ROOT, "tasks.json")
MIN_INTERVAL = 30          # 防手抖：最短 30 秒
MAX_TASKS = 50             # 防暴涨


def _load() -> list:
    """Read persisted tasks; fail closed on corruption rather than erase claims."""
    try:
        with open(STORE, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return []
    if not isinstance(data, list) or any(not isinstance(t, dict) for t in data):
        raise ValueError("task store must contain a list of task objects")
    return data


def _save(tasks: list) -> None:
    """Atomically persist and flush a transaction before execution is allowed."""
    directory = os.path.dirname(os.path.abspath(STORE))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="tasks-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(tasks, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, STORE)
    finally:
        # A failed replace must not leave a partial store or reusable claim.
        if os.path.exists(tmp):
            os.unlink(tmp)


def _scheduled_at(run_at: float | None, delay_s, now: float, *, updating: bool = False) -> float:
    """Validate explicit absolute deadlines without confusing them with relative delays."""
    if run_at is None:
        return now + max(0, int(delay_s or 0))
    if isinstance(run_at, bool) or not isinstance(run_at, Real) or not math.isfinite(run_at) or run_at < 0:
        raise ValueError("run_at must be a finite nonnegative epoch number")
    # add defaults delay_s to zero; update uses None for omission.
    if (updating and delay_s is not None) or (not updating and delay_s not in (None, 0)):
        raise ValueError("run_at and delay_s are mutually exclusive")
    return float(run_at)


def _cadence(value: str) -> str:
    """Reject misspelled scheduling modes rather than silently changing timing."""
    if value not in ("after_completion", "fixed_rate"):
        raise ValueError("cadence must be after_completion or fixed_rate")
    return value


def _outcome_snapshot(outcome: dict | None) -> dict | None:
    """Copy only typed delivery evidence; never store arbitrary tool payloads or secrets."""
    if outcome is None:
        return None
    boolean_keys = {"delivery_attempted", "delivery_confirmed", "delivery_failed", "delivery_unknown"}
    list_keys = {"delivery_message_ids", "last_tools"}
    if not isinstance(outcome, dict) or set(outcome) - boolean_keys - list_keys:
        raise ValueError("invalid outcome metadata")
    result = {}
    for key, value in outcome.items():
        if key in boolean_keys:
            if type(value) is not bool:
                raise ValueError("delivery outcome flags must be booleans")
            result[key] = value
        else:
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise ValueError("delivery message IDs and tool names must be string lists")
            result[key] = list(value)
    return result


@_locked
def add(title: str, goal: str, scope: str, interval_s: int = 120,
        repeat: str = "every", delay_s: int = 0,
        brief: str = "", resume_prompt: str = "",
        trigger_kind: str = "time", trigger_script: str = "",
        trigger_every_s: int = 60, origin: str = "", *,
        run_at: float | None = None, cadence: str = "after_completion") -> dict:
    """Upsert by normalized scope/title; never resume paused or running work."""
    tasks = _load()
    title = (title or "未命名任务")[:60]
    scope = scope or ""
    now = time.time()
    deadline = _scheduled_at(run_at, delay_s, now)
    cadence = _cadence(cadence)
    for task in tasks:
        if task.get("scope") == scope and task.get("title") == title \
                and task.get("status") in ("active", "paused"):
            task.update({
                "goal": (goal or task.get("goal") or title)[:1200],
                "interval_s": max(MIN_INTERVAL, int(interval_s or 120)),
                "repeat": repeat if repeat in ("once", "every") else "every",
                "cadence": cadence,
                "trigger_kind": trigger_kind if trigger_kind in ("time", "script") else "time",
                "trigger_script": (trigger_script or "")[:6000],
                "trigger_every_s": max(MIN_INTERVAL, int(trigger_every_s or 60)),
            })
            # Empty optional context must not erase the model's continuation.
            if brief:
                task["brief"] = brief[:600]
            if resume_prompt:
                task["resume_prompt"] = resume_prompt[:1200]
            if origin:
                task["origin"] = origin
            if task.get("execution_state") != "running":
                task["next_run_at"] = deadline
            _save(tasks)
            return task
    if len(tasks) >= MAX_TASKS:
        # Only terminal history can be evicted; paused/claimed tasks are durable.
        tasks = [t for t in tasks if t.get("status") not in ("done", "cancelled")
                 or t.get("execution_state") == "running"]
        if len(tasks) >= MAX_TASKS:
            raise ValueError("task limit reached; cancel an existing task first")
    task = {
        "id": uuid.uuid4().hex[:8],
        "title": (title or "未命名任务")[:60],
        "goal": (goal or title or "")[:1200],
        "brief": (brief or goal or title or "")[:600],
        "trigger_kind": trigger_kind if trigger_kind in ("time", "script") else "time",
        "trigger_script": (trigger_script or "")[:6000],
        "trigger_every_s": max(MIN_INTERVAL, int(trigger_every_s or 60)),
        "resume_prompt": (resume_prompt or "")[:1200],
        "scope": scope or "",
        "interval_s": max(MIN_INTERVAL, int(interval_s or 120)),
        "repeat": repeat if repeat in ("once", "every") else "every",
        "cadence": cadence,
        "status": "active",
        "nudges": 0,
        "created_at": now,
        "next_run_at": deadline,
        "execution_state": "idle",
        "origin": origin or "",
        "note": "",
    }
    tasks.append(task)
    _save(tasks)
    return task


SCRIPT_TIMEOUT = 20
SCRIPT_DIR = ROOT


def run_trigger_script(code: str) -> tuple:
    """执行任务触发脚本；返回 (是否成立, 说明)。

    判定：退出码 0 且 stdout 非空、且不是 false/0/no/none/null 时成立。
    """
    if not (code or "").strip():
        return False, "empty script"
    try:
        os.makedirs(SCRIPT_DIR, exist_ok=True)
        path = os.path.join(SCRIPT_DIR, "trigger_tmp.py")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(code)
        env = {"PATH": "/usr/bin:/bin", "HOME": "/root", "LANG": "C.UTF-8"}
        proc = subprocess.run([sys.executable, path], capture_output=True, text=True,
                              timeout=SCRIPT_TIMEOUT, cwd=SCRIPT_DIR, env=env)
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if proc.returncode != 0:
            return False, ("exit %d: %s" % (proc.returncode, err[-160:]))
        if not out:
            return False, "empty output"
        if out.lower() in ("false", "0", "no", "none", "null"):
            return False, "false"
        return True, out[:300]
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as exc:                                 # noqa: BLE001
        return False, repr(exc)[:160]


class PlanError(ValueError):
    """Carry a stable tool error code without exposing another scope's records."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _plan_task(items: list, task_id: str, scope: str) -> dict:
    """Resolve exact task/scope identity; blank scopes must never mean all tasks."""
    if not isinstance(task_id, str) or not task_id or not isinstance(scope, str) or not scope:
        raise PlanError("invalid_arguments", "task_id and scope must be nonempty strings")
    for task in items:
        if task.get("id") == task_id and task.get("scope") == scope:
            return task
    # Foreign and missing records are deliberately indistinguishable.
    raise PlanError("not_found", "task not found in current scope")


def _plan_steps(steps: list) -> list:
    """Validate and copy data-only steps once for both persistence and reads."""
    if not isinstance(steps, list):
        raise PlanError("invalid_arguments", "steps must be a list")
    result, identifiers = [], set()
    for step in steps:
        if not isinstance(step, dict) or set(step) != {"id", "content", "status"}:
            raise PlanError("invalid_arguments", "each step requires only id, content, status")
        identifier, content, status = step["id"], step["content"], step["status"]
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", identifier):
            raise PlanError("invalid_arguments", "step id must be 1..64 letters, digits or underscores")
        if identifier in identifiers:
            raise PlanError("invalid_arguments", "step ids must be unique")
        if not isinstance(content, str) or not content.strip() or len(content) > 1000:
            raise PlanError("invalid_arguments", "step content must be nonempty and at most 1000 characters")
        if not isinstance(status, str) or status not in ("pending", "in_progress", "completed"):
            raise PlanError("invalid_arguments", "invalid step status")
        identifiers.add(identifier)
        result.append({"id": identifier, "content": content, "status": status})
    return result


def _plan_snapshot(task: dict) -> dict:
    """Return a detached snapshot; legacy records start at revision zero."""
    plan = task.get("plan", {"revision": 0, "steps": []})
    if not isinstance(plan, dict) or type(plan.get("revision")) is not int or plan["revision"] < 0:
        raise PlanError("invalid_plan", "stored plan revision is invalid")
    return {"task_id": task["id"], "revision": plan["revision"],
            "steps": _plan_steps(plan.get("steps"))}


@_locked
def read_plan(task_id: str, scope: str) -> dict:
    """Read the existing task's plan without creating a record or changing state."""
    return _plan_snapshot(_plan_task(_load(), task_id, scope))


@_locked
def update_plan(task_id: str, scope: str, steps: list, expected_revision: int) -> dict:
    """Replace a scoped plan with CAS under the existing store transaction lock.

    Completed steps remain completed when supplied; parallel in-progress steps
    are permitted. Plan completion never changes scheduler or goal state.
    """
    if type(expected_revision) is not int or expected_revision < 0:
        raise PlanError("invalid_arguments", "expected_revision must be an integer >= 0")
    items = _load()
    task = _plan_task(items, task_id, scope)
    current = _plan_snapshot(task)
    if expected_revision != current["revision"]:
        raise PlanError("revision_conflict", "plan revision changed; read the current plan before updating")
    replacement = _plan_steps(steps)
    if replacement == current["steps"]:
        # Exact-revision retries do not generate redundant writes or revisions.
        return current
    task["plan"] = {"revision": current["revision"] + 1, "steps": replacement}
    _save(items)
    return _plan_snapshot(task)


@_locked
def due(now: float | None = None) -> list:
    """List due candidates without executing scripts; claim before any effects.

    Listing does not execute trigger scripts.
    The caller must claim each candidate immediately before executing it.
    """
    now = now if now is not None else time.time()
    items = [t for t in _load() if t.get("status") == "active"
             and t.get("execution_state") != "running"
             and float(t.get("next_run_at") or 0) <= now]
    items.sort(key=lambda t: float(t.get("next_run_at") or 0))
    return items[:5]


@_locked
def claim(task_id: str, now: float | None = None) -> dict | None:
    """Persist exclusive ownership before trigger/tools; never expire a claim.

    A crash leaves running work blocked for explicit human reconciliation, since
    timing out and retrying unknown mutating operations could duplicate effects.
    """
    now = now if now is not None else time.time()
    tasks = _load()
    for task in tasks:
        if task.get("id") != task_id:
            continue
        if task.get("status") != "active" or task.get("execution_state") == "running" \
                or float(task.get("next_run_at") or 0) > now:
            return None
        task.update(execution_state="running", claim_id=uuid.uuid4().hex, claimed_at=now)
        _save(tasks)
        if task.get("trigger_kind") == "script":
            ok, why = run_trigger_script(str(task.get("trigger_script") or ""))
            task["trigger_last"] = why
            if not ok:
                # Trigger probes are conditions, not task attempts; no nudge/done.
                task["execution_state"] = "idle"
                task["next_run_at"] = now + max(MIN_INTERVAL, int(task.get("trigger_every_s") or 60))
                if why not in ("false", "empty output"):
                    # A failed script may already have mutated state: no retry.
                    task["execution_state"] = "failed"
                    task["status"] = "paused"
                    task["note"] = why[:300]
                task.pop("claim_id", None)
                _save(tasks)
                return None
            _save(tasks)
        return task
    return None


@_locked
def mark(task_id: str, result: str = "", schedule_next: bool = True, *,
         success: bool = True, claim_id: str = "", outcome: dict | None = None) -> bool:
    """Acknowledge only a claimed task; repeat acknowledgements are no-ops.

    The obsolete mark(result) form deliberately cannot bulk-update tasks.
    Pass claim_id to reject stale completions after an explicit resume/reclaim.
    Failures pause rather than blindly replay possibly mutating operations.
    """
    evidence = _outcome_snapshot(outcome)
    tasks = _load()
    now = time.time()
    for task in tasks:
        if task.get("id") != task_id or task.get("execution_state") != "running":
            continue
        if claim_id and task.get("claim_id") != claim_id:
            return False
        task["execution_state"] = "succeeded" if success else "failed"
        task["completed_at"] = now
        task["note"] = (result or "")[:300]
        if evidence is not None:
            task["last_outcome"] = evidence
        task["nudges"] = int(task.get("nudges") or 0) + 1
        task.pop("claim_id", None)
        # task_done/cancel/pause from inside the task must not be undone here.
        if task.get("status") == "active":
            if not success:
                task["status"] = "paused"
            elif task.get("repeat") == "once":
                task["status"] = "done"
                task["finished_at"] = now
            elif not schedule_next:
                task["status"] = "paused"
            else:
                interval = max(MIN_INTERVAL, int(task.get("interval_s") or 120))
                if task.get("cadence") == "fixed_rate":
                    # Preserve the last scheduled tick, skipping missed ticks without catch-up bursts.
                    anchor = float(task["next_run_at"])
                    ticks = max(1, math.floor((now - anchor) / interval) + 1)
                    task["next_run_at"] = anchor + ticks * interval
                else:
                    # Missing cadence on legacy records retains completion-relative behavior.
                    task["next_run_at"] = now + interval
        _save(tasks)
        return True
    return False


@_locked
def set_status(task_id: str = "", title: str = "", status: str = "done",
               note: str = "") -> list:
    """Pause/resume/cancel one selector; an id takes precedence over a title.

    Resuming an idle failed task is explicit authorization for another attempt.
    Running claims remain blocked: status changes cannot cancel in-flight effects
    or prove a crashed attempt safe to replay.
    """
    if status not in ("active", "paused", "done", "cancelled"):
        raise ValueError("invalid task status")
    tasks = _load()
    hit = []
    for task in tasks:
        matches = task.get("id") == task_id if task_id else bool(title and task.get("title") == title)
        if not matches:
            continue
        # Terminal tasks stay terminal; repeating cancellation is idempotent.
        if task.get("status") in ("done", "cancelled") and task.get("status") != status:
            continue
        if task.get("status") != status:
            task["status"] = status
            if status in ("done", "cancelled"):
                task["finished_at"] = time.time()
            elif status == "active" and task.get("execution_state") != "running":
                task["execution_state"] = "idle"
                task["next_run_at"] = time.time()
        if note:
            task["note"] = note[:300]
        hit.append(task)
    if hit:
        _save(tasks)
    return hit


@_locked
def listing(scope: str = "", only_active: bool = True, status: str = "") -> list:
    """Query scope and status; only_active=False includes paused/terminal work."""
    items = _load()
    if scope:
        items = [t for t in items if t.get("scope") == scope]
    if status:
        items = [t for t in items if t.get("status") == status]
    elif only_active:
        items = [t for t in items if t.get("status") == "active"]
    return items


@_locked
def update(task_id: str = "", title: str = "", brief: str = "",
           resume_prompt: str = "", interval_s: int = 0,
           next_step_note: str = "", trigger_kind: str = "",
           trigger_script: str = "", trigger_every_s: int = 0,
           origin: str = "", repeat: str = "", delay_s: int | None = None,
           goal: str = "", *, run_at: float | None = None,
           cadence: str = "") -> list:
    """Incrementally update active/paused work, preserving claims and context.

    Existing positional arguments retain their meaning. New scheduling options
    are opt-in; delay_s=0 means now, while None leaves the deadline unchanged.
    """
    deadline = _scheduled_at(run_at, delay_s, time.time(), updating=True) if run_at is not None or delay_s is not None else None
    if cadence:
        _cadence(cadence)
    tasks = _load()
    hit = []
    for task in tasks:
        if task.get("status") not in ("active", "paused"):
            continue
        matches = task.get("id") == task_id if task_id else bool(title and task.get("title") == title)
        if matches:
            if goal:
                task["goal"] = goal[:1200]
            if origin:
                task["origin"] = origin
            if repeat in ("once", "every"):
                task["repeat"] = repeat
            if cadence:
                task["cadence"] = cadence
            if deadline is not None and task.get("execution_state") != "running":
                task["next_run_at"] = deadline
            if brief:
                task["brief"] = brief[:600]
            if resume_prompt:
                task["resume_prompt"] = resume_prompt[:1200]
            if interval_s:
                task["interval_s"] = max(MIN_INTERVAL, int(interval_s))
            if next_step_note:
                task["note"] = next_step_note[:300]
            if trigger_kind in ("time", "script"):
                task["trigger_kind"] = trigger_kind
            if trigger_script:
                task["trigger_script"] = trigger_script[:6000]
            if trigger_every_s:
                task["trigger_every_s"] = max(MIN_INTERVAL, int(trigger_every_s))
            hit.append(task)
    if hit:
        _save(tasks)
    return hit
