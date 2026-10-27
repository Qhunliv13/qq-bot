"""Pure, text-free provenance summary of caller-supplied conversation evidence.

This module selects no reply, changes no history, and infers no completion or
permissions. Legacy display strings never establish a speaker, scope or date.
The caller retains the full original history alongside this partial summary.
"""

from collections import Counter
from datetime import datetime, timezone
import math


def _timestamp(value):
    """Accept finite numeric source timestamps without guessing units or dates."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _scope(row):
    """Read explicit source scope only; never borrow the destination's identity."""
    if not isinstance(row, dict):
        return None
    value = row.get("origin_scope")
    if value is None:
        value = row.get("scope")
    return str(value) if value is not None and str(value) else None


def _time_view(value, now):
    """Project exact supplied time and age; future evidence keeps negative age."""
    value = _timestamp(value)
    if value is None:
        return None
    try:
        iso = datetime.fromtimestamp(value, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        # Finite but unsupported epochs remain source evidence, not invented dates.
        iso = None
    return {"ts": value, "iso": iso,
            "age_seconds": None if now is None else now - value}


def _record_kind(row):
    """Classify native metadata only, leaving legacy display lines unknown."""
    record = row.get("record") if isinstance(row, dict) else None
    if not isinstance(record, dict):
        return "unknown"
    sender = record.get("sender")
    sender = sender if isinstance(sender, dict) else {}
    if record.get("kind") == "assistant_delivery" or sender.get("role") == "assistant":
        return "assistant"
    # Native incoming records have transport identity and a body. Display-name or
    # line-prefix matching must never turn old prose into a human input record.
    if (record.get("id") and sender.get("id")
            and isinstance(record.get("body"), str)
            and sender.get("role") not in ("bot", "system", "tool")):
        return "input"
    return "unknown"


def snapshot(scope, history, messages, clock, trigger=None, deliveries=None, *,
             scope_history=None, now=None, task=None):
    """Summarize supplied source identities without replacing the original data.

    ``scope_history`` optionally supplies an independently loaded current-scope
    view when a global history window contains no recent local rows. All coverage
    is partial: a last-known input/output is not a claim about unseen records.
    ``now`` must be an explicit host epoch; the clock object is not guessed to be
    an incoming-message timestamp. No I/O, caller mutation, text copies, reply
    filtering, time threshold or mandatory behavior is introduced.
    """
    scope = str(scope)
    rows = list(history) if isinstance(history, (list, tuple)) else []
    current = list(messages) if isinstance(messages, (list, tuple)) else []
    now = _timestamp(now)
    counts = Counter()
    latest = {}
    unknown_scope = 0
    for row in rows:
        origin = _scope(row)
        if origin is None:
            unknown_scope += 1
            continue
        counts[origin] += 1
        stamp = _timestamp(row.get("ts"))
        if stamp is not None and (origin not in latest or stamp > latest[origin]):
            latest[origin] = stamp
    # A separately supplied scope view is authoritative about its origin only
    # when a row omits scope; contradictory explicit origin is never reassigned.
    scoped = ([row for row in scope_history
               if isinstance(row, dict) and _scope(row) in (None, scope)]
              if isinstance(scope_history, (list, tuple))
              else [row for row in rows if _scope(row) == scope])
    last = {"activity": None, "input": None, "assistant": None}
    unknown_kind = 0
    for row in scoped:
        stamp = _timestamp(row.get("ts"))
        kind = _record_kind(row)
        unknown_kind += kind == "unknown"
        if stamp is None:
            continue
        if last["activity"] is None or stamp > last["activity"]:
            last["activity"] = stamp
        if kind in ("input", "assistant") and (last[kind] is None or stamp > last[kind]):
            last[kind] = stamp
    kind = trigger.get("kind") if isinstance(trigger, dict) else None
    turn_kind = (kind if isinstance(kind, str) and kind else
                 "foreground" if current else "task" if task is not None else "unspecified")
    # Retain incoming transport IDs verbatim; timing or history cannot invent one.
    ids = [row["id"] for row in current
           if isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"]]
    return {
        "source": "conversation_state", "trust": "untrusted_data", "scope": scope,
        "turn_kind": turn_kind, "current_input_count": len(current),
        "current_message_ids": ids,
        "history_scope_counts": [
            {"scope": origin, "count": count,
             "last_event_at": _time_view(latest.get(origin), now)}
            for origin, count in sorted(counts.items())],
        "history_unknown_scope_count": unknown_scope,
        "current_scope_last_activity": _time_view(last["activity"], now),
        "current_scope_last_known_input": _time_view(last["input"], now),
        "current_scope_last_known_assistant_output": _time_view(last["assistant"], now),
        "observed_delivery_count": len(deliveries) if isinstance(deliveries, (list, tuple)) else 0,
        "coverage": {"history": "supplied_records_only",
                     "scope_history": "supplied_records_only",
                     "scope_record_count": len(scoped),
                     "scope_unknown_kind_count": unknown_kind},
    }
