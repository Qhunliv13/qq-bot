"""Deterministic serialized-context budgets, independent of message counts.

Units are Unicode characters of compact JSON (not guessed provider tokens).
Raw archives remain untouched; source IDs/scopes are retained on clipped views.
"""
from __future__ import annotations

from copy import deepcopy
import json

# Reuse the former summary threshold as an explicit input-context length default.
DEFAULT_CONTEXT_CHARS = 600000


def size(value):
    """Measure exactly the compact UTF-8-compatible JSON presentation in characters."""
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def limits():
    """Read one optional operator budget; never infer model capacity from its name."""
    from .runtime_settings import load_settings
    settings = load_settings() or {}
    options = settings.get("context_memory", {})
    if not isinstance(options, dict) or set(options) - {"max_context_chars"}:
        raise ValueError("invalid_context_memory")
    maximum = options.get("max_context_chars", DEFAULT_CONTEXT_CHARS)
    if type(maximum) is not int or maximum < 4096:
        raise ValueError("max_context_chars must be an integer >= 4096")
    return {"max_context_chars": maximum, "summary_chars": max(256, maximum // 8)}


def suffix(rows, max_chars):
    """Select the newest whole records by serialized length, not a row quota.

    An individually oversized row is not silently altered or admitted over budget.
    Coverage states the exclusion, and its exact archive remains tool-readable.
    """
    if type(max_chars) is not int or max_chars < 0:
        raise ValueError("max_chars must be a nonnegative integer")
    used = 2  # The enclosing JSON array's brackets.
    start = len(rows)
    for index in range(len(rows) - 1, -1, -1):
        cost = size(rows[index]) + (1 if start < len(rows) else 0)
        if used + cost > max_chars:
            break
        start = index
        used += cost
    selected = deepcopy(list(rows[start:]))
    return selected, {"unit": "serialized_unicode_characters", "max_chars": max_chars,
                      "used_chars": size(selected), "returned": len(selected),
                      "omitted": start, "more_available": start > 0,
                      "oversized_latest": bool(rows) and start == len(rows)}
