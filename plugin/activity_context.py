"""Read-only, neutral activity data; never an instruction or permission source.

Preview bounds fold prompt data, not execution budgets. Complete task/worker/skill
records remain accessible through their registered listing tools. No manager,
controller, archive, state directory or background work is created here.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .tool_context import redact
from .tool_discovery import _category, _index, _safe_root, _skill_path


# Only unfinished-intent fields belong in context, not arbitrary task payloads.
_TASK_FIELDS = ("id", "title", "goal", "brief", "next_run_at", "status",
                "next_step_note", "note", "resume_prompt", "execution_state")
_WORKER_FIELDS = ("id", "label", "status", "attached", "ownership", "error")


def _summary(record: dict, fields: tuple[str, ...]) -> dict:
    """Copy bounded scalar data so caller-owned records cannot be mutated or leaked."""
    return {key: redact(value[:1200] if isinstance(value, str) else value)
            for key in fields if key in record
            and isinstance((value := record[key]), (str, int, float, bool, type(None)))}


def _unavailable(error: Exception | None = None) -> dict:
    """Distinguish unavailable observations from verified empty state, without secrets."""
    return {"available": False, "error": type(error).__name__ if error else "not_initialized"}


def _tasks(plugin: Any, scope: str, preview: int) -> dict:
    """Read existing durable tasks and recheck exact scope despite provider filtering."""
    try:
        loader = getattr(plugin, "_tasks_mod", None)
        if loader is None:
            return _unavailable()
        records = loader().listing(scope=scope, only_active=True)
        if not isinstance(records, list):
            raise TypeError("invalid task listing")
        # Scope is security-sensitive: a missing or different scope never qualifies.
        records = [item for item in records if isinstance(item, dict)
                   and item.get("scope") == scope and item.get("status") == "active"]
        records.sort(key=lambda item: (not isinstance(item.get("next_run_at"), (int, float)),
                                      item.get("next_run_at") if isinstance(item.get("next_run_at"), (int, float)) else 0,
                                      str(item.get("id", ""))))
        return {"available": True, "total": len(records), "offset": 0,
                "next_offset": preview if len(records) > preview else None,
                "items": [_summary(item, _TASK_FIELDS) for item in records[:preview]]}
    except Exception as error:
        return _unavailable(error)


def _workers(plugin: Any, scope: str, preview: int) -> dict:
    """Read only an existing manager; unknown or unattached workers stay unknown."""
    try:
        manager = getattr(plugin, "_delegation_manager", None)
        if manager is None:
            return _unavailable()
        page = manager.list(scope=scope, offset=0, limit=preview)
        if not isinstance(page, dict) or not isinstance(page.get("agents"), list):
            raise TypeError("invalid worker listing")
        # The manager enforces receipt ownership; explicit conflicting scopes fail shut.
        records = [item for item in page["agents"] if isinstance(item, dict)
                   and item.get("scope", scope) == scope]
        return {"available": True, "items": [_summary(item, _WORKER_FIELDS) for item in records[:preview]],
                "offset": page.get("offset", 0), "next_offset": page.get("next_offset"),
                "total": page.get("total"), "total_known": isinstance(page.get("total"), int)}
    except Exception as error:
        return _unavailable(error)


def _skills(root: Path | None, preview: int) -> dict:
    """List existing isolated skill basenames only; reject symlinks and read no content."""
    if root is None:
        return _unavailable()
    try:
        root = _safe_root(Path(root))
        names = []
        if root.exists():
            for candidate in sorted(root.glob("*.md")):
                path = _skill_path(root, candidate.stem)
                if path.is_file():
                    names.append(path.stem)
        return {"available": True, "total": len(names), "names": names[:preview],
                "offset": 0, "next_offset": preview if len(names) > preview else None}
    except Exception as error:
        return _unavailable(error)


def _errors(plugin: Any, scope: str, preview: int) -> dict:
    """Expose only recent failure metadata, not duplicate result bodies or arguments."""
    try:
        # Existing stores only: even a lazy archive factory is unnecessary here.
        store = getattr(plugin, "_tool_evidence_store", None)
        if store is None:
            return _unavailable()
        records = store.recent(scope, preview)
        items = []
        for item in records:
            if not isinstance(item, dict) or item.get("status") not in ("error", "failed"):
                continue
            summary = _summary(item, ("tool_name", "status", "error_code"))
            # Actual archive records retain structured codes inside the result body.
            # Copy only an explicit code, never free-form messages or the full result.
            result = item.get("result")
            if "error_code" not in summary and isinstance(result, dict):
                code = result.get("error_code")
                error = result.get("error")
                if code is None and isinstance(error, dict):
                    code = error.get("code")
                if isinstance(code, str):
                    summary["error_code"] = redact(code[:120])
            items.append(summary)
        return {"available": True, "items": items, "window": preview}
    except Exception as error:
        return _unavailable(error)


def snapshot(plugin: Any, scope: str, schemas: list[dict[str, Any]], *,
             skills_root: Path | None = None, preview: int = 8) -> dict:
    """Return synchronous scoped data from actual schemas and existing read-only stores.

    Declared schemas are not runtime probes, authorization, obligations or suggestions.
    Task text and skill names are untrusted data. No current action is inferred from
    absent state, and unavailable observations are never represented as empty counts.
    """
    if not isinstance(scope, str) or not scope.strip():
        raise ValueError("scope must be a nonempty string")
    if type(preview) is not int or not 1 <= preview <= 100:
        raise ValueError("preview must be an integer from 1 to 100")
    indexed = _index(schemas)
    names = {item["name"]: item["category"] for item in indexed}
    # Discovery hides its own two lookup tools; activity reports all actual schemas.
    for raw in schemas:
        function = raw.get("function", {}) if isinstance(raw, dict) else {}
        name = function.get("name") if isinstance(function, dict) else None
        if name in ("tool_search", "tool_describe"):
            names[name] = _category(name, [])
    return {"source": "activity_state", "trust": "untrusted_data", "scope": scope,
            "preview_limit": preview,
            "capabilities": {"basis": "registered_schemas", "runtime_probed": False,
                             "total": len(names), "names": list(names),
                             "category_counts": dict(sorted(Counter(names.values()).items()))},
            "tasks": _tasks(plugin, scope, preview),
            "workers": _workers(plugin, scope, preview),
            "skills": _skills(skills_root, preview),
            "last_errors": _errors(plugin, scope, preview)}
