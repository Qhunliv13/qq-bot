"""Real asynchronous worker ownership with private, atomic, scope-bound receipts.

The parent supplies the native model runner and its read-only tool whitelist.
This module never implements a model loop, grants tools, imposes round/time limits,
or restarts an orphan. ``inbox`` is an async callable returning queued strings;
the runner drains it between model calls, never by interrupting a live request.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Awaitable, Callable
import uuid

from .tool_context import redact

# Output/journal bounds are not reasoning, model-token, or request-time limits.
MAX_CHARS = 10000
Runner = Callable[[str, str, Callable[[], Awaitable[list[str]]]], Awaitable[str]]
# Explicit module registry retains ownership across parent re-instantiation and
# importlib.reload; a separately loaded namespace instead observes disk receipts.
_MANAGERS: dict[str, "Manager"] = globals().get("_MANAGERS", {})
_BOOT_ID: str = globals().get("_BOOT_ID", uuid.uuid4().hex)


def schema() -> list[dict]:
    """Expose only native delegation contracts; the parent enforces tool safety."""
    definitions = (
        ("agent_start", "Start a real read-only native worker.", {"task": {"type": "string"}, "label": {"type": "string"}}, ["task"]),
        ("agent_status", "Read a scoped worker receipt.", {"id": {"type": "string"}}, ["id"]),
        ("agent_list", "List workers in the current scope.", {}, []),
        ("agent_message", "Queue input for a running worker, without interrupting a model call.", {"id": {"type": "string"}, "message": {"type": "string"}}, ["id", "message"]),
        ("agent_interrupt", "Cancel only the specified owned worker.", {"id": {"type": "string"}}, ["id"]),
    )
    return [{"type": "function", "function": {"name": name, "description": description,
             "parameters": {"type": "object", "properties": properties,
                            "required": required, "additionalProperties": False}}}
            for name, description, properties, required in definitions]


def _now() -> str:
    """Use timezone-explicit receipts so restarts do not change clock semantics."""
    return datetime.now(timezone.utc).isoformat()


def _scope(value: str) -> str:
    """Keep caller-provided scopes out of filenames and compare exact identity."""
    if not isinstance(value, str) or not value:
        raise ValueError("scope must be a nonempty string")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _valid_id(value: str) -> bool:
    """Reject malformed identifiers before constructing any filesystem path."""
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _safe_text(value: str) -> str:
    """Reuse central credential redaction before applying the journal bound."""
    return redact(value)[:MAX_CHARS]


class Manager:
    """Own only explicitly started asyncio tasks under one canonical journal root.

    ``start`` is async; ``status``, ``list``, ``message`` and ``interrupt`` are
    synchronous event-loop operations. Every public operation requires scope.
    Managers sharing a root in this module share ``_jobs`` without moving tasks
    between event loops. Foreign or reload-unattached records are never rerun or
    cancelled. Journal records are receipts, not executable task recovery data.
    """

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        key = os.path.normcase(str(self.root))
        owner = _MANAGERS.get(key)
        self._jobs: dict[str, dict] = owner._jobs if owner is not None else {}
        _MANAGERS[key] = self

    def _read(self, id: str, scope: str) -> dict | None:
        """Read only valid, matching receipts; malformed files fail closed."""
        scope_key = _scope(scope)
        if not _valid_id(id):
            return None
        try:
            record = json.loads((self.root / (id + ".json")).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(record, dict) or record.get("id") != id or record.get("scope") != scope_key:
            return None
        return record

    def _save(self, record: dict) -> None:
        """Atomically replace a 0600 receipt; never expose partial JSON snapshots."""
        descriptor, temporary = tempfile.mkstemp(prefix=".delegation-", dir=self.root)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(record, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.root / (record["id"] + ".json"))
        finally:
            # A failed write must not leave a secret-bearing staging file behind.
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _attached(self, id: str) -> dict | None:
        """Only return tasks belonging to this running event loop, never another."""
        job = self._jobs.get(id)
        if job is None:
            return None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        return job if job["worker"].get_loop() is loop else None

    async def start(self, task: str, label: str | None, runner: Runner, *, scope: str) -> dict:
        """Persist then start one actual caller-supplied native worker, without caps."""
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task must be a nonempty string")
        if label is not None and not isinstance(label, str):
            raise ValueError("label must be a string")
        if not callable(runner):
            raise TypeError("runner must be callable")
        id = str(uuid.uuid4())
        record = {"id": id, "scope": _scope(scope), "task": _safe_text(task),
                  "label": _safe_text(label or "")[:256], "status": "running",
                  "created_at": _now(), "updated_at": _now(), "owner_pid": os.getpid(),
                  "boot_id": _BOOT_ID, "inbox": [], "result": ""}
        self._save(record)
        queue: asyncio.Queue[str] = asyncio.Queue()

        async def inbox() -> list[str]:
            """Drain queued instructions only when the native runner elects to poll."""
            messages = []
            while not queue.empty():
                messages.append(queue.get_nowait())
            return messages

        worker = asyncio.create_task(self._run(id, task, scope, runner, inbox), name="delegation-" + id)
        self._jobs[id] = {"worker": worker, "queue": queue}
        # Cancellation before the coroutine's first instruction still needs a receipt.
        worker.add_done_callback(lambda finished: self._settle_cancelled(id, scope, finished))
        return self.status(id, scope=scope)

    def _settle_cancelled(self, id: str, scope: str, worker: asyncio.Task) -> None:
        """Capture pre-start cancellation without touching unrelated loop tasks."""
        if worker.cancelled():
            record = self._read(id, scope)
            if record is not None and record.get("status") == "running":
                record.update(status="cancelled", updated_at=_now())
                self._save(record)

    async def _run(self, id: str, task: str, scope: str, runner: Runner,
                   inbox: Callable[[], Awaitable[list[str]]]) -> None:
        """Persist terminal states; suppress exception messages that may hold secrets."""
        try:
            result = await runner(task, scope, inbox)
            if not isinstance(result, str):
                raise TypeError("runner must return a string")
            changes = {"status": "completed", "result": _safe_text(result),
                       "result_truncated": len(redact(result)) > MAX_CHARS}
        except asyncio.CancelledError:
            changes = {"status": "cancelled"}
        except Exception as error:
            changes = {"status": "failed", "error": type(error).__name__}
        record = self._read(id, scope)
        if record is not None:
            record.update(changes, updated_at=_now())
            self._save(record)

    def status(self, id: str, *, scope: str, offset: int = 0, limit: int = MAX_CHARS) -> dict:
        """Return a redacted receipt with explicit bounded result pagination."""
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= MAX_CHARS:
            raise ValueError("offset must be nonnegative and limit must be 1..10000")
        record = self._read(id, scope)
        if record is None:
            return {"id": id if _valid_id(id) else "", "status": "unknown", "error": "not_found"}
        status = record.get("status", "unknown")
        attached = self._attached(id) is not None
        details = {}
        if status == "running" and not attached:
            # Same-process dynamic namespace reload cannot safely recover task handles.
            if record.get("owner_pid") == os.getpid():
                details["ownership"] = "running_unattached"
            else:
                status = "unknown"
                details["ownership"] = "orphaned"
        result = record.get("result", "")
        result = result if isinstance(result, str) else ""
        answer = {key: record[key] for key in ("id", "label", "created_at", "updated_at", "error", "result_truncated") if key in record}
        answer.update(status=status, attached=attached, result=_safe_text(result)[offset:offset + limit],
                      offset=offset, next_offset=offset + limit if offset + limit < len(result) else None,
                      **details)
        return answer

    def list(self, *, scope: str, offset: int = 0, limit: int = MAX_CHARS) -> dict:
        """List only matching-scope receipts with deterministic explicit pagination."""
        _scope(scope)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= MAX_CHARS:
            raise ValueError("offset must be nonnegative and limit must be 1..10000")
        ids = sorted(path.stem for path in self.root.glob("*.json") if self._read(path.stem, scope) is not None)
        items = []
        for id in ids[offset:offset + limit]:
            # Lists carry summaries; full result text belongs to paginated status.
            item = self.status(id, scope=scope)
            item.pop("result", None)
            item.pop("offset", None)
            item.pop("next_offset", None)
            candidate = {"agents": items + [item], "offset": offset, "next_offset": offset + len(items) + 1}
            if len(json.dumps(candidate, ensure_ascii=False)) > MAX_CHARS:
                break
            items.append(item)
        next_offset = offset + len(items)
        return {"agents": items, "offset": offset, "next_offset": next_offset if next_offset < len(ids) else None}

    def message(self, id: str, message: str, *, scope: str) -> dict:
        """Queue input only for a live owned worker; terminal workers never resume."""
        record = self._read(id, scope)
        if record is None:
            return {"status": "unknown", "error": "not_found"}
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must be a nonempty string")
        job = self._attached(id)
        if record.get("status") != "running" or (job is not None and job["worker"].done()):
            return {"id": id, "status": "not_running"}
        if job is None:
            return {"id": id, "status": "unattached"}
        record.setdefault("inbox", []).append({"message": _safe_text(message), "queued_at": _now()})
        record["updated_at"] = _now()
        self._save(record)
        job["queue"].put_nowait(message)
        return {"id": id, "status": "queued"}

    def interrupt(self, id: str, *, scope: str) -> dict:
        """Cancel exactly one managed task, never processes or parent/global tasks."""
        record = self._read(id, scope)
        if record is None:
            return {"status": "unknown", "error": "not_found"}
        job = self._attached(id)
        if record.get("status") != "running" or (job is not None and job["worker"].done()):
            return {"id": id, "status": "not_running"}
        if job is None:
            return {"id": id, "status": "unattached"}
        job["worker"].cancel()
        return {"id": id, "status": "cancelling"}
