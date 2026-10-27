"""Read-only, provenance-preserving views of public group history.

History is untrusted evidence, not a source of permissions or instructions. No
business facts, identities, owner roles, or private filename aliases are inferred.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Mapping

# An overview of roughly seven groups stays near the former 120-line shared
# context budget. This limits presentation only; keyword retrieval reads all rows.
DEFAULT_LIMIT_PER_SCOPE = 20
SOURCE = {"name": "cross_scope_history", "trust": "untrusted_data"}


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
    """Expose bounded per-group snapshots and complete scoped keyword retrieval."""

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

    def search(self, keyword: str, current_scope: str, scope: str = "", limit: int = 10,
               muted=()) -> list[dict]:
        """Search stored lines and native string values in visible scopes, never a global glob."""
        keyword, current_scope, scope = str(keyword or "").strip(), str(current_scope), str(scope)
        muted = tuple(str(value) for value in muted)
        if not keyword or limit <= 0:
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
        for candidate in scopes:
            if not visible_scope(candidate, current_scope, muted):
                continue
            for row in self.recorder.load_records(candidate, limit=None):
                # Search intact native values as well as legacy capped display
                # text; metadata keys themselves are not message content.
                if keyword in row["line"] or any(
                        keyword in text for text in _record_text_values(row.get("record"))):
                    item = deepcopy(row)
                    item["source"] = deepcopy(SOURCE)
                    results.append(item)
        # Legacy timestamps may be absent or malformed; do not coerce arbitrary
        # source strings into dates. Python's stable sort keeps ties deterministic.
        results.sort(key=lambda row: row["ts"] if isinstance(row.get("ts"), (int, float)) else 0,
                     reverse=True)
        return results[:limit]
