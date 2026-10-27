"""Append-only history prefixes within the existing recent-history budgets.

The default prompt suffix contains 120 through 239 entries when enough history
exists; shorter histories are retained intact. Its start advances on 120-entry
boundaries to keep unchanged source prefixes stable between calls. The live
capacity is 2000 entries. This module does not batch messages, delay replies,
truncate individual entries, or change admission/delivery policy.
"""

from __future__ import annotations

from collections.abc import Sequence


def append_history(
    histories: dict[str, list[str]],
    origins: dict[str, int],
    scope: str,
    line: str,
    capacity: int = 2000,
) -> None:
    """Append one source entry and track indices evicted by the existing live cap.

    Every writer, including delivered replies and membership observations, must
    use this helper so a full buffer does not reset block alignment each append.
    A restored scope starts at logical origin zero; origins must accompany its
    history during in-process handoff. No second copy of historical text is kept.
    """
    if capacity <= 0:
        raise ValueError("capacity must be positive")
    history = histories.setdefault(scope, [])
    origins.setdefault(scope, 0)
    history.append(line)
    dropped = max(0, len(history) - capacity)
    if dropped:
        # Retain logical positions when the physical list shifts at its live cap.
        del history[:dropped]
        origins[scope] += dropped


def history_snapshot(
    history: Sequence[str], origin: int, minimum: int = 120
) -> tuple[str, ...]:
    """Return an immutable chronological suffix containing the latest minimum entries.

    The start advances only at blocks derived from minimum. With enough source
    entries, the result contains minimum through 2 * minimum - 1 entries; shorter
    histories are returned intact. Entries themselves are never altered. These
    are entry-count bounds, not token or character budgets. The caller snapshots
    before appending the current input, preserving its separate source identity.
    """
    if minimum <= 0:
        raise ValueError("minimum must be positive")
    if origin < 0:
        raise ValueError("origin must be nonnegative")
    # Align absolute source indices, not the capped list length: otherwise every
    # eviction shifts the prefix even though the apparent history length is fixed.
    aligned = ((origin + len(history) - minimum) // minimum) * minimum
    start = max(origin, aligned) - origin
    return tuple(history[start:])
