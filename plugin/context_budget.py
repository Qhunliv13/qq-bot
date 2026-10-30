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
    return {"max_context_chars": maximum, "summary_chars": maximum * 2 // 5}


def allocation(maximum, fixed_chars, *, overhead_chars=2048):
    """Split actual conversational space 60/40 after fixed request costs.

    The ratios concern serialized lengths, not message counts or all-time archive
    volume. Complete native records can underfill the raw region by one boundary
    record; unspent summary space never evicts more raw chat than its own budget.
    """
    if any(type(value) is not int or value < 0
           for value in (maximum, fixed_chars, overhead_chars)):
        raise ValueError("context allocation requires nonnegative integers")
    available = max(0, maximum - fixed_chars - overhead_chars)
    raw = available * 3 // 5
    return {"available_chars": available, "raw_chars": raw,
            "summary_chars": available - raw, "raw_fraction": 0.6,
            "summary_archive_fraction": 0.4, "overhead_chars": overhead_chars}


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
    total_chars = sum(size(row) for row in rows)
    retained_chars = sum(size(row) for row in selected)
    return selected, {"unit": "serialized_unicode_characters", "max_chars": max_chars,
                      "used_chars": size(selected), "returned": len(selected),
                      "omitted": start, "more_available": start > 0,
                      "source_chars": total_chars, "retained_source_chars": retained_chars,
                      "omitted_source_chars": total_chars - retained_chars,
                      "retained_source_fraction": retained_chars / total_chars if total_chars else 1.0,
                      "archive_deleted_records": 0,
                      "oversized_latest": bool(rows) and start == len(rows)}
