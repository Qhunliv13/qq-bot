#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""她的后台任务存储（2026-10-19）。

设计要点：
  · 单一存储：/root/higan/tasks/tasks.json（持久 ✓ 在虚拟机里 ✓）
  · 任务字段：id / title / goal / scope（群号或 private:QQ）/ interval_s / repeat /
              status（active|paused|done|cancelled）/ next_run_at / nudges / origin
  · `due()` 只列候选；执行前必须 `claim(id)`，执行后 `mark(id, result, ...)`。
  · `repeat="once"` 仅执行成功后 done；`repeat="every"` 从完成时间起按 interval_s 循环。
  · 失败暂停，运行中的认领永久保留，避免不确定副作用被自动重试（单进程线程安全）。
"""

from __future__ import annotations

from functools import wraps
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid

# The plugin runs scheduler and tool calls in threads of one process.
_LOCK = threading.RLock()


def _locked(function):
    """Serialize read/modify/write transactions in this single-process store."""
    @wraps(function)
    def wrapper(*args, **kwargs):
        with _LOCK:
            return function(*args, **kwargs)
    return wrapper

ROOT = "/root/higan/tasks"
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


@_locked
def add(title: str, goal: str, scope: str, interval_s: int = 120,
        repeat: str = "every", delay_s: int = 0,
        brief: str = "", resume_prompt: str = "",
        trigger_kind: str = "time", trigger_script: str = "",
        trigger_every_s: int = 60, origin: str = "") -> dict:
    """Upsert by normalized scope/title; never resume paused or running work."""
    tasks = _load()
    title = (title or "未命名任务")[:60]
    scope = scope or ""
    now = time.time()
    for task in tasks:
        if task.get("scope") == scope and task.get("title") == title \
                and task.get("status") in ("active", "paused"):
            task.update({
                "goal": (goal or task.get("goal") or title)[:1200],
                "interval_s": max(MIN_INTERVAL, int(interval_s or 120)),
                "repeat": repeat if repeat in ("once", "every") else "every",
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
                task["next_run_at"] = now + max(0, int(delay_s or 0))
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
        "status": "active",
        "nudges": 0,
        "created_at": now,
        "next_run_at": now + max(0, int(delay_s or 0)),
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
    """[script-trigger] 跑她自己写的触发脚本；返回 (是否成立, 说明)。

    判定：退出码 0 且 stdout 非空、且不是 false/0/no/none ⇒ 成立 ✓
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


@_locked
def due(now: float | None = None) -> list:
    """List due candidates without executing scripts; claim before any effects.

    Unlike the old poller, listing cannot run arbitrary trigger code repeatedly.
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
         success: bool = True, claim_id: str = "") -> bool:
    """Acknowledge only a claimed task; repeat acknowledgements are no-ops.

    The obsolete mark(result) form deliberately cannot bulk-update tasks.
    Pass claim_id to reject stale completions after an explicit resume/reclaim.
    Failures pause rather than blindly replay possibly mutating operations.
    """
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
                task["next_run_at"] = now + max(MIN_INTERVAL, int(task.get("interval_s") or 120))
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
           goal: str = "") -> list:
    """Incrementally update active/paused work, preserving claims and context.

    Existing positional arguments retain their meaning. New scheduling options
    are opt-in; delay_s=0 means now, while None leaves the deadline unchanged.
    """
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
            if delay_s is not None and task.get("execution_state") != "running":
                task["next_run_at"] = time.time() + max(0, int(delay_s))
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
