"""Whole-entry history suffixes measured by shared serialized-length budgets.

Default live-cache retention uses the configured character budget, never a row
quota. Explicit count capacities and minimum-based snapshots remain available
only for legacy callers. Raw archive storage and individual entries are unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence

from .context_budget import limits, suffix


def append_history(
    histories: dict[str, list[str]],
    origins: dict[str, int],
    scope: str,
    line: str,
    capacity: int | None = None,
    *,
    max_chars: int | None = None,
) -> None:
    """Append one source entry and retain a whole-entry serialized-length suffix.

    Default retention reads the shared configured character budget. An explicit
    numeric capacity preserves old integrations only; ``max_chars`` always takes
    precedence when supplied. Origins track evicted entries for legacy snapshots,
    not a runtime row quota. Cache eviction never deletes archive evidence.
    """
    if capacity is not None and (type(capacity) is not int or capacity <= 0):
        raise ValueError("capacity must be a positive integer")
    if max_chars is None and capacity is None:
        max_chars = limits()["max_context_chars"]
    if max_chars is not None and (type(max_chars) is not int or max_chars < 0):
        raise ValueError("max_chars must be a nonnegative integer")
    history = histories.setdefault(scope, [])
    origins.setdefault(scope, 0)
    history.append(line)
    if max_chars is not None:
        # Shared serialization counts whitespace, Unicode and JSON escaping;
        # do not approximate with text length or a maximum number of messages.
        _, coverage = suffix(history, max_chars)
        dropped = coverage["omitted"]
    else:
        dropped = max(0, len(history) - capacity)
    if dropped:
        # Retain logical positions when the physical list shifts at its live cap.
        del history[:dropped]
        origins[scope] += dropped


def history_snapshot(
    history: Sequence[str], origin: int, minimum: int = 120,
    *, max_chars: int | None = None,
) -> tuple[str, ...]:
    """Return an immutable chronological suffix without changing source entries.

    ``max_chars`` selects solely by the shared compact-JSON length budget. If it
    is omitted, the legacy minimum-based block alignment remains for explicit old
    integrations. The caller snapshots before appending current input to preserve
    its separate source identity; neither path modifies archived records.
    """
    if origin < 0:
        raise ValueError("origin must be nonnegative")
    if max_chars is not None:
        selected, _ = suffix(history, max_chars)
        return tuple(selected)
    if minimum <= 0:
        raise ValueError("minimum must be positive")
    # Align absolute source indices, not the capped list length: otherwise every
    # eviction shifts the prefix even though the apparent history length is fixed.
    aligned = ((origin + len(history) - minimum) // minimum) * minimum
    start = max(origin, aligned) - origin
    return tuple(history[start:])
