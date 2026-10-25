#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""她的后台任务存储（2026-10-19）。

设计要点：
  · 单一存储：/root/higan/tasks/tasks.json（持久 ✓ 在虚拟机里 ✓）
  · 任务字段：id / title / goal / scope（群号或 private:QQ）/ interval_s / repeat /
              status（active|done|cancelled）/ next_run_at / nudges / created_at / note
  · 「不断发提示词让她继续」：`due()` 返回到点任务，插件据此把目标重新投给她，直到
    她调用 `task_done` 把 status 改成 done（或 cancel）✓
  · 定时/重复：`repeat="once"` 到点后自动 done；`repeat="every"` 按 interval_s 循环 ✓
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid

ROOT = "/root/higan/tasks"
STORE = os.path.join(ROOT, "tasks.json")
MIN_INTERVAL = 30          # 防手抖：最短 30 秒
MAX_TASKS = 50             # 防暴涨


def _load() -> list:
    """读全部任务（容错：坏文件当作空 ✓）。"""
    try:
        with open(STORE, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, list) else []
    except Exception:                                     # noqa: BLE001
        return []


def _save(tasks: list) -> None:
    """原子写盘（先写临时文件再替换 ✓）。"""
    os.makedirs(ROOT, exist_ok=True)
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(tasks, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, STORE)


def add(title: str, goal: str, scope: str, interval_s: int = 120,
        repeat: str = "every", delay_s: int = 0,
        brief: str = "", resume_prompt: str = "",
        trigger_kind: str = "time", trigger_script: str = "",
        trigger_every_s: int = 60) -> dict:
    """新建任务（同一 scope+title 视为同一任务，避免重复堆叠 ✓）。"""
    tasks = _load()
    for task in tasks:
        if task.get("scope") == scope and task.get("title") == title \
                and task.get("status") == "active":
            task["goal"] = goal or task.get("goal")
            task["interval_s"] = max(MIN_INTERVAL, int(interval_s or 120))
            task["repeat"] = repeat if repeat in ("once", "every") else "every"
            task["next_run_at"] = time.time() + int(delay_s or 0)
            _save(tasks)
            return task
    if len(tasks) >= MAX_TASKS:
        done = [t for t in tasks if t.get("status") != "active"]
        tasks = done[: len(done) // 2] + [t for t in tasks if t.get("status") == "active"]
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
        "created_at": time.time(),
        "next_run_at": time.time() + int(delay_s or 0),
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


def due(now: float | None = None) -> list:
    """到点且仍需继续的任务（时间触发 + 脚本触发都判定 ✓）。"""
    now = now if now is not None else time.time()
    items = []
    for task in _load():
        if task.get("status") != "active":
            continue
        if float(task.get("next_run_at") or 0) > now:
            continue
        if task.get("trigger_kind") == "script":
            ok, why = run_trigger_script(str(task.get("trigger_script") or ""))
            task["trigger_last"] = why
            if not ok:
                task["next_run_at"] = now + int(task.get("trigger_every_s") or 60)
                _save_all(task)
                continue
        items.append(task)
    items.sort(key=lambda t: float(t.get("next_run_at") or 0))
    return items[:5]


def _save_all(updated: dict) -> None:
    """把单条任务的字段改动写回（按 id 匹配 ✓）。"""
    tasks = _load()
    for task in tasks:
        if task.get("id") == updated.get("id"):
            task.update({k: v for k, v in updated.items()
                         if k in ("next_run_at", "trigger_last")})
    _save(tasks)


def mark(result: str = "", schedule_next: bool = True) -> None:
    """记录一次催促的结果（推进 next_run_at / 统计 nudges ✓）。"""
    tasks = _load()
    now = time.time()
    for task in tasks:
        if task.get("status") != "active":
            continue
        if float(task.get("next_run_at") or 0) > now:
            continue
        task["nudges"] = int(task.get("nudges") or 0) + 1
        task["note"] = (result or "")[:300]
        if task.get("repeat") == "once":
            task["status"] = "done"
            task["finished_at"] = now
        else:
            task["next_run_at"] = now + int(task.get("interval_s") or 120)
    _save(tasks)


def set_status(task_id: str = "", title: str = "", status: str = "done",
               note: str = "") -> list:
    """把任务标记为 done / cancelled（按 id 或 title 匹配 ✓）。"""
    tasks = _load()
    hit = []
    for task in tasks:
        if task.get("status") != "active":
            continue
        if (task_id and task.get("id") == task_id) or (title and task.get("title") == title):
            task["status"] = status if status in ("done", "cancelled") else "done"
            task["finished_at"] = time.time()
            if note:
                task["note"] = note[:300]
            hit.append(task)
    if hit:
        _save(tasks)
    return hit


def listing(scope: str = "", only_active: bool = True) -> list:
    """列任务（可按 scope 过滤 ✓）。"""
    items = _load()
    if scope:
        items = [t for t in items if t.get("scope") == scope]
    if only_active:
        items = [t for t in items if t.get("status") == "active"]
    return items


def update(task_id: str = "", title: str = "", brief: str = "",
           resume_prompt: str = "", interval_s: int = 0,
           next_step_note: str = "", trigger_kind: str = "",
           trigger_script: str = "", trigger_every_s: int = 0) -> list:
    """她自己改写任务：简述 / 续做提示词 / 间隔（多步任务每轮都能修正方向 ✓）。"""
    tasks = _load()
    hit = []
    for task in tasks:
        if task.get("status") != "active":
            continue
        if (task_id and task.get("id") == task_id) or (title and task.get("title") == title):
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
