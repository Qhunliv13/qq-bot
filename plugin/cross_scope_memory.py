"""Read-only, provenance-preserving views of public group history.

History is untrusted evidence, not a source of permissions or instructions. No
business facts, identities, owner roles, or private filename aliases are inferred.
"""

from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Mapping

from .context_budget import suffix

# The legacy snapshot API bounds each group's overview independently.
# Unified timelines use DEFAULT_SHARED_LIMIT; keyword retrieval reads all rows.
DEFAULT_LIMIT_PER_SCOPE = 20
SOURCE = {"name": "cross_scope_history", "trust": "untrusted_data"}
# One shared presentation budget, independent of the legacy per-group overview.
DEFAULT_SHARED_LIMIT = 240
# Cache alignment adds at most one eighth of the existing presentation minimum;
# it never changes archived evidence, visibility, or native message identity.
DEFAULT_SHARED_CACHE_BLOCK = max(1, DEFAULT_SHARED_LIMIT // 8)
UNIFIED_SOURCE = {"name": "unified_group_memory", "trust": "untrusted_data"}


def _group(scope: str) -> bool:
    """Recognize the recorder's positive ASCII-numeric group namespace only."""
    return bool(re.fullmatch(r"[0-9]+", scope)) and int(scope) > 0


def _record_text_values(value):
    """Yield native JSON string values without matching structural keys or fabricating text."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        # Native body and nested quote strings can exceed the legacy rendered-line cap.
        for child in value.values():
            yield from _record_text_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _record_text_values(child)


def _chronological_key(row):
    """Order finite stored timestamps only, leaving unknown dates explicitly unknown."""
    timestamp = row.get("ts")
    valid = (not isinstance(timestamp, bool)
             and (isinstance(timestamp, int)
                  or isinstance(timestamp, float) and math.isfinite(timestamp)))
    # Stable sorting retains archive order for ties and unknown dates; zero is
    # not a replacement timestamp, since the leading flag distinguishes it.
    return (1, timestamp) if valid else (0, 0)


def visible_scope(candidate: str, current_scope: str, muted=()) -> bool:
    """Allow unmuted groups and only the caller's exact native private scope."""
    candidate, current_scope = str(candidate), str(current_scope)
    if candidate in {str(scope) for scope in muted}:
        return False
    if _group(candidate):
        return True
    # Do not decode private_ filenames: only the native current scope is trusted.
    return candidate == current_scope and candidate.startswith("private:")


class CrossScopeMemory:
    """Expose one shared timeline, legacy bounded group views, and scoped keyword retrieval."""

    def __init__(self, recorder, histories: Mapping | None = None,
                 source_records: Mapping | None = None, *,
                 limit_per_scope: int = DEFAULT_LIMIT_PER_SCOPE):
        if limit_per_scope <= 0:
            raise ValueError("limit_per_scope must be positive")
        self.recorder = recorder
        self.histories = histories
        self.source_records = source_records
        self.limit_per_scope = limit_per_scope

    def _records(self, scope: str, histories: Mapping | None,
                 source_records: Mapping | None, limit: int | None) -> list[dict]:
        """Use exact live lines when loaded; metadata requires exact line alignment."""
        if histories is None or scope not in histories:
            return self.recorder.load_records(scope, limit=limit)
        live = list(histories[scope])
        typed = list((source_records or {}).get(scope, ()))
        rows = []
        # Rendered live text alone cannot establish which persisted occurrence
        # supplied it. Only explicitly aligned typed records may supply identity.
        for index in range(len(live) - 1, -1, -1):
            line = live[index]
            if not isinstance(line, str) or not line:
                continue
            row = {"scope": scope, "ts": None, "line": line}
            if len(typed) == len(live) and isinstance(typed[index], dict):
                # Structured maps must carry their own matching line; otherwise
                # their position cannot prove provenance across partial reloads.
                source = typed[index]
                if source.get("line") == line:
                    row = {"scope": scope, "ts": source.get("ts"), "line": line}
                    if "record" in source:
                        row["record"] = deepcopy(source["record"])
            rows.append(row)
        rows.reverse()
        return rows if limit is None else rows[-limit:]

    def snapshot(self, current_scope: str, *, muted=(), histories: Mapping | None = None,
                 source_records: Mapping | None = None) -> list[dict]:
        """Return deterministic public-group overviews without writes or text truncation."""
        current_scope = str(current_scope)
        # Freeze iterable policy inputs once so generator-based callers cannot
        # accidentally apply the mute filter only to the first candidate.
        muted = tuple(str(value) for value in muted)
        histories = self.histories if histories is None else histories
        source_records = self.source_records if source_records is None else source_records
        scopes = set(self.recorder.group_scopes())
        # Live-only groups are visible before their first successful disk append.
        if histories is not None:
            scopes.update(str(scope) for scope in histories if _group(str(scope)))
        result = []
        for scope in sorted(scopes, key=lambda value: (int(value), value)):
            if scope == current_scope or not visible_scope(scope, current_scope, muted):
                continue
            # One extra row proves truncation without loading entire disk histories.
            rows = self._records(scope, histories, source_records, self.limit_per_scope + 1)
            if not rows:
                continue
            result.append({"scope": scope, "source": deepcopy(SOURCE),
                           "records": deepcopy(rows[-self.limit_per_scope:]),
                           "coverage": {"returned": min(len(rows), self.limit_per_scope),
                                        "limit": self.limit_per_scope,
                                        "more_available": len(rows) > self.limit_per_scope}})
        return result

    def unified_snapshot(self, *, exclude_messages=(), exclude_message_ids=(),
                         limit: int = DEFAULT_SHARED_LIMIT,
                         cache_block_size: int = 0,
                         max_chars: int | None = None) -> dict:
        """Merge persisted public groups; unknown dates stay unknown, never inferred.

        Exclusions are native (scope, record.id) pairs or context dictionaries
        carrying scope and id/message_id. Bare IDs cannot establish provenance.
        This disk-only view deliberately ignores live caches and mute policy.
        Optional alignment retains at least the latest ``limit`` eligible rows
        while adding fewer than ``cache_block_size`` older rows. Exact archive
        counts, not a mutable cache or invented timestamps, anchor each block.
        With ``max_chars``, every eligible archive row participates in the shared
        serialized-length suffix instead: legacy count limits/alignment do not
        apply. ``omitted_records`` preserves all older eligible evidence for the
        caller's summary input; callers must remove it before building a prompt.
        """
        if max_chars is not None:
            if type(max_chars) is not int or max_chars < 0:
                raise ValueError("max_chars must be a nonnegative integer")
        else:
            # Count parameters remain solely for explicit legacy/tool callers.
            if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
                raise ValueError("limit must be a positive integer")
            if (isinstance(cache_block_size, bool) or not isinstance(cache_block_size, int)
                    or cache_block_size < 0):
                raise ValueError("cache_block_size must be a nonnegative integer")
        excluded = []
        for values in (exclude_messages, exclude_message_ids):
            for value in values:
                if isinstance(value, dict):
                    scope = value.get("scope")
                    message_id = value.get("id", value.get("message_id"))
                elif isinstance(value, (tuple, list)) and len(value) == 2:
                    scope, message_id = value
                else:
                    # A bare ID or legacy display string is not a scoped identity.
                    continue
                if scope is not None and message_id is not None:
                    excluded.append((str(scope), message_id))
        rows = []
        scope_count = 0
        more_available = False
        total_count = 0
        snapshot_loader = getattr(self.recorder, "load_record_snapshot", None)
        alignment_available = bool(max_chars is None and cache_block_size
                                   and callable(snapshot_loader))
        scopes = {str(scope) for scope in self.recorder.group_scopes()
                  if _group(str(scope))}
        for scope in sorted(scopes, key=lambda value: (int(value), value)):
            if alignment_available:
                # Filtering before bounded retention keeps the newest minimum
                # even when many current-batch IDs occur in the archived tail.
                snapshot = snapshot_loader(
                    scope, limit=limit + cache_block_size,
                    exclude_message_ids=tuple(message_id for pair_scope, message_id in excluded
                                              if pair_scope == scope))
                if not snapshot.get("available", False):
                    # A partial count cannot anchor a global block. Re-read via
                    # the existing exact-budget API instead of fabricating one.
                    result = self.unified_snapshot(exclude_messages=excluded, limit=limit)
                    result["coverage"].update(cache_block_size=cache_block_size,
                                              cache_padding=0,
                                              cache_alignment_available=False)
                    return result
                stored = snapshot["records"]
                total_count += snapshot["count"]
            else:
                # Preserve the legacy exact-budget contract for existing callers
                # and recorder adapters that cannot supply trustworthy counts.
                stored = self.recorder.load_records(
                    scope, limit=None if max_chars is not None else limit + 1)
            if not stored:
                continue
            scope_count += 1
            if max_chars is None:
                more_available = more_available or len(stored) > limit
            for source in stored:
                record = source.get("record")
                origin = source["scope"]
                if isinstance(record, dict) and "id" in record and any(
                        str(origin) == pair_scope and record["id"] == message_id
                        for pair_scope, message_id in excluded):
                    continue
                row = deepcopy(source)
                row["origin_scope"] = origin
                rows.append(row)

        rows.sort(key=_chronological_key)
        if max_chars is not None:
            # Select only after exclusions and the global chronology are known;
            # no per-group cap may hide evidence from this length-based view.
            selected, coverage = suffix(rows, max_chars)
            archive_scopes = sorted(scopes, key=lambda value: (int(value), value))
            returned_scopes = {str(row["scope"]) for row in selected}
            coverage.update(limit=max_chars, scope_count=scope_count,
                            archive_scopes=archive_scopes,
                            returned_scopes=[scope for scope in archive_scopes
                                             if scope in returned_scopes])
            return {"source": deepcopy(UNIFIED_SOURCE), "mode": "one_shared_timeline",
                    "records": selected, "coverage": coverage,
                    "omitted_records": deepcopy(rows[:coverage["omitted"]])}
        width = limit
        if alignment_available and total_count >= limit:
            # Absolute eligible row count fixes the head until the next block;
            # every append still includes every row the exact last-limit view had.
            width += (total_count - limit) % cache_block_size
        selected = rows[-width:]
        if alignment_available:
            more_available = total_count > len(selected)
        else:
            more_available = more_available or len(rows) > limit
        # Archive discovery and selected evidence are distinct: a busy group may
        # occupy the whole shared window without erasing another group's store.
        # These are observed archive IDs, not a joined-group or participant census.
        archive_scopes = sorted(scopes, key=lambda value: (int(value), value))
        returned_scopes = {str(row["scope"]) for row in selected}
        coverage = {"returned": len(selected), "limit": limit,
                    "more_available": more_available, "scope_count": scope_count,
                    "archive_scopes": archive_scopes,
                    "returned_scopes": [scope for scope in archive_scopes
                                        if scope in returned_scopes]}
        if cache_block_size:
            # Coverage follows the record body; changing diagnostics therefore
            # cannot invalidate the immutable history prefix preceding them.
            coverage.update(cache_block_size=cache_block_size,
                            cache_padding=max(0, len(selected) - limit),
                            cache_alignment_available=alignment_available)
        return {"source": deepcopy(UNIFIED_SOURCE), "mode": "one_shared_timeline",
                "records": selected, "coverage": coverage}

    def search(self, keyword: str, current_scope: str, scope: str = "", limit: int = 10,
               muted=(), offset: int = 0) -> list[dict]:
        """Search visible archives or page one explicitly selected scope, newest first.

        An omitted keyword never opens an unscoped archive scan. Explicit reads
        share the same native-private authorization, mute policy and provenance
        as keyword searches; the selected scope is not inferred from query text.
        Exact native message IDs are searchable even when stored as numbers.
        Only repeated nonempty native IDs within the same scope establish a
        duplicate occurrence; text, quote IDs and forward contents never do.
        Offset applies after matching, identity deduplication and stable sorting.
        """
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a nonnegative integer")
        keyword, current_scope, scope = str(keyword or "").strip(), str(current_scope), str(scope)
        muted = tuple(str(value) for value in muted)
        if (not keyword and not scope) or limit <= 0:
            return []
        if scope:
            if not visible_scope(scope, current_scope, muted):
                return []
            scopes = [scope]
        else:
            scopes = self.recorder.group_scopes()
            if current_scope.startswith("private:"):
                # The caller's own native scope is safe; foreign private files
                # are neither discovered nor filename-decoded.
                scopes.append(current_scope)
        results = []
        seen = set()
        for candidate in scopes:
            if not visible_scope(candidate, current_scope, muted):
                continue
            for row in self.recorder.load_records(candidate, limit=None):
                record = row.get("record")
                message_id = record.get("id") if isinstance(record, dict) else None
                native_id = (isinstance(message_id, str) and bool(message_id.strip())
                             or isinstance(message_id, int) and not isinstance(message_id, bool))
                if native_id:
                    # First stored occurrence wins, even if a later duplicate
                    # has different text. Missing IDs never merge legacy rows.
                    identity = (str(candidate), type(message_id), message_id)
                    if identity in seen:
                        continue
                    seen.add(identity)
                # A numeric native message ID has no string leaf, so support its
                # exact lookup explicitly without treating numeric times as text.
                # Quote/body hits remain their own parent records, not new forwards.
                if (not keyword or native_id and keyword == str(message_id)
                        or keyword in row["line"] or any(
                            keyword in text for text in _record_text_values(record))):
                    item = deepcopy(row)
                    item["source"] = deepcopy(SOURCE)
                    results.append(item)
        # Use the timeline's finite timestamp rule, not ingestion strings, bools
        # or non-finite numbers. Original timestamps and metadata remain intact.
        results.sort(key=_chronological_key, reverse=True)
        return results[offset:offset + limit]
