#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按来源作用域追加和读取 JSONL 聊天档案。

Recorder.append() 保存最多 2000 字符的显示文本，并独立保留完整原始元数据；
写入不按 MAX_LINES 滚动裁剪。load() 按旧到新返回显示文本，load_records()
返回带来源 scope 的原存储文本与元数据，limit=None 时读取全部有效记录。
group_scopes() 只发现已有的正整数群号档案，不猜测私聊文件对应的身份。
search() 提供显示文本关键词检索，受 MAX_SCOPES 和调用方结果数量限制。
"""

from __future__ import annotations

from collections import deque
from copy import deepcopy
import json
import os
import re
import time
from threading import RLock

if __package__:
    from .runtime_paths import data_root
else:
    from plugin.runtime_paths import data_root

MAX_LINES = 5000
MAX_SCOPES = 30

# Serialize the check-and-append transaction across Recorder instances in this process.
_APPEND_LOCK = RLock()


class HistoryPersistenceError(Exception):
    """Report a sanitized checked-write failure without exposing archive text or paths."""

    def __init__(self, error: str, *, uncertain: bool = False) -> None:
        self.error = error
        # A failed write/fsync can leave bytes behind; callers must not assume a rollback.
        self.uncertain = uncertain
        super().__init__(error)


class Recorder:
    """按 scope 存聊天记录（JSONL，一行一条）。"""

    def __init__(self, root: str | None = None) -> None:
        """Initialize the explicit archive or configured data root, never another account's path."""
        root = str(data_root() / "history") if root is None else root
        self.root = root
        # One detached tail/count per file; the lock also protects future threaded readers.
        self._snapshot_lock = RLock()
        self._snapshot_cache = {}
        # Stat-validated native identities avoid rescanning an unchanged archive per append.
        self._append_cache = {}
        try:
            os.makedirs(root, exist_ok=True)
        except Exception:                                 # noqa: BLE001
            pass

    def _path(self, scope: str) -> str:
        """scope → 文件名（把 : 等非法字符换掉）。"""
        safe = re.sub(r"[^0-9A-Za-z_.-]", "_", str(scope or "default"))[:60]
        return os.path.join(self.root, safe + ".jsonl")

    def append(self, scope: str, line: str, *, record: dict | None = None) -> dict:
        """Return a best-effort receipt, not None; checked callers must use append_checked.

        Legacy callers remain exception-free. Failure receipts contain only the
        error class and whether bytes may have reached disk, never input or paths.
        """
        try:
            return self.append_checked(scope, line, record=record)
        except Exception as exc:                            # noqa: BLE001
            return {"persisted": False,
                    "error": exc.error if isinstance(exc, HistoryPersistenceError) else type(exc).__name__,
                    "uncertain": bool(getattr(exc, "uncertain", False))}

    def append_checked(self, scope: str, line: str, *, record: dict | None = None) -> dict:
        """Append validated UTF-8 JSONL durably or raise HistoryPersistenceError.

        Display text retains the legacy 2000-character cap; record metadata is
        detached and stored in full. Only exact scope/kind/source/native ID
        duplicates are suppressed, never repeated text or records without IDs.
        The process lock covers validation, identity lookup, write and fsync.
        Malformed or unterminated archives are never repaired or overwritten.
        A failure after write begins is uncertain: bytes are retained, and a
        later retry must revalidate and fsync before acknowledging a duplicate.
        This is process-thread safety, not a cross-process transaction protocol.
        """
        uncertain = False
        path = None
        with _APPEND_LOCK:
            try:
                text = str(line or "").strip()
                if not text:
                    raise ValueError("empty history line")
                exact_scope = str(scope)
                item = {"ts": int(time.time()), "scope": exact_scope, "line": text[:2000]}
                if record is not None:
                    # Preserve complete bodies and unknown native fields without mutating callers.
                    item["record"] = deepcopy(record)
                encoded = (json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
                identity = self._record_identity(exact_scope, item.get("record"))
                path = self._path(scope)
                identities, stamp, synced = self._checked_archive(path)
                duplicate = identity is not None and identity in identities
                if duplicate and synced:
                    return {"persisted": True, "duplicate": True}
                # Append mode never truncates old evidence, including after a failed write.
                with open(path, "ab") as handle:
                    opened = self._archive_stamp(os.fstat(handle.fileno()))
                    if (stamp is not None and opened != stamp) or (stamp is None and opened[2] != 0):
                        # A newly created empty file is safe; unexpected existing bytes are not.
                        raise HistoryPersistenceError("ArchiveChanged")
                    if not duplicate:
                        uncertain = True
                        if handle.write(encoded) != len(encoded):
                            raise OSError("short history write")
                    # An uncached duplicate may be the residue of a prior failed fsync.
                    uncertain = True
                    handle.flush()
                    os.fsync(handle.fileno())
                    after = self._archive_stamp(os.fstat(handle.fileno()))
                if identity is not None:
                    identities.add(identity)
                self._append_cache[path] = {"stamp": after, "identities": identities, "synced": True}
                return {"persisted": True, "duplicate": duplicate}
            except Exception as exc:                        # noqa: BLE001
                if path is not None:
                    # Never trust cached identities or durability after any failed transaction.
                    self._append_cache.pop(path, None)
                error = exc.error if isinstance(exc, HistoryPersistenceError) else type(exc).__name__
                # Suppress exception chaining: native errors can embed file paths or message text.
                raise HistoryPersistenceError(error, uncertain=uncertain) from None

    @staticmethod
    def _archive_stamp(stat) -> tuple:
        """Detect replacement, append, same-size rewrite and truncation using cheap metadata."""
        # Windows path stat and descriptor stat disagree on ctime semantics after writes.
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)

    @staticmethod
    def _record_identity(scope: str, record) -> tuple | None:
        """Use typed native IDs partitioned by exact record kind/source; never infer from text."""
        if not isinstance(record, dict):
            return None
        native_id = record.get("id")
        if type(native_id) not in (str, int) or native_id == "":
            # None, empty IDs, booleans and container-shaped IDs carry no reliable native identity.
            return None
        partition = ["kind" in record, record.get("kind"), "source" in record, record.get("source")]
        return (scope, json.dumps(partition, ensure_ascii=False, allow_nan=False, sort_keys=True),
                type(native_id).__name__, native_id)

    def _checked_archive(self, path: str) -> tuple:
        """Validate complete existing JSONL once per stat change; return native identities.

        Old rows without explicit scope can prove identity only for their literal
        filename scope. A sanitized filename cannot prove which original scope
        produced a legacy row, so such rows are preserved but not deduplicated.
        """
        try:
            stamp = self._archive_stamp(os.stat(path))
        except FileNotFoundError:
            return set(), None, False
        cached = self._append_cache.get(path)
        if cached is not None and cached["stamp"] == stamp:
            return cached["identities"], stamp, cached["synced"]
        identities = set()
        legacy_scope = os.path.basename(path)[:-6]
        with open(path, "rb") as handle:
            for raw, _ in self._snapshot_lines(handle):
                if not raw.endswith((b"\n", b"\r")):
                    raise HistoryPersistenceError("IncompleteArchive")
                text = raw.decode("utf-8")
                if not text.strip():
                    # Legacy blank separator rows carry no identity and do not damage JSONL.
                    continue
                item = json.loads(text, parse_constant=self._reject_nonfinite)
                if not isinstance(item, dict) or not isinstance(item.get("line"), str):
                    raise HistoryPersistenceError("InvalidArchive")
                # Also reject exponent-overflow Infinity and escaped lone surrogates in old rows.
                json.dumps(item, ensure_ascii=False, allow_nan=False).encode("utf-8")
                stored_scope = item.get("scope")
                if stored_scope is None and "_" not in legacy_scope and len(legacy_scope) < 60:
                    # Underscores/truncation can conceal sanitization collisions in legacy paths.
                    stored_scope = legacy_scope
                identity = self._record_identity(stored_scope, item.get("record"))
                if isinstance(stored_scope, str) and identity is not None:
                    identities.add(identity)
            if self._archive_stamp(os.fstat(handle.fileno())) != stamp:
                raise HistoryPersistenceError("ArchiveChanged")
        # A scan proves structure, not durability; the next acknowledged write must fsync.
        self._append_cache[path] = {"stamp": stamp, "identities": identities, "synced": False}
        return identities, stamp, False

    @staticmethod
    def _reject_nonfinite(value):
        """Reject JSON's nonstandard NaN/Infinity tokens without echoing archive data."""
        raise ValueError("nonfinite history value")

    def load(self, scope: str, limit: int | None = 200) -> list:
        """Read archived display lines chronologically; None imposes no row-count cap."""
        path = self._path(scope)
        if not os.path.exists(path):
            return []
        try:
            lines: list = []
            with open(path, encoding="utf-8") as handle:
                for raw in handle:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        lines.append(str(json.loads(raw).get("line") or ""))
                    except Exception:                      # noqa: BLE001
                        continue
            selected = [x for x in lines if x]
            return selected if limit is None else selected[-limit:]
        except Exception:                                  # noqa: BLE001
            return []

    def group_scopes(self) -> list[str]:
        """Discover only existing positive ASCII-numeric group stores, never private filename guesses."""
        try:
            # Numeric filenames are the existing group naming contract, not a business allowlist.
            scopes = [name[:-6] for name in os.listdir(self.root)
                      if name.endswith(".jsonl")
                      and re.fullmatch(r"[0-9]+", name[:-6])
                      and int(name[:-6]) > 0
                      and os.path.isfile(os.path.join(self.root, name))]
            return sorted(scopes, key=lambda scope: (int(scope), scope))
        except OSError:
            # Missing/unreadable history must not create files or interrupt replies.
            return []

    def load_records(self, scope: str, limit: int | None = 20) -> list[dict]:
        """Read exact stored text/metadata; legacy lines have no invented sender or message ID."""
        return self.load_record_snapshot(scope, limit=limit)["records"]

    def load_record_snapshot(self, scope: str, limit: int | None = 20, *,
                             exclude_message_ids=()) -> dict:
        """Read one scope's eligible tail and full eligible count from the same JSONL scan.

        Exclusions compare only native record IDs, never displayed text or inferred
        identities. Nonpositive limits preserve load_records' no-I/O behavior:
        records/count are empty/zero, available is true, and no count is requested.
        """
        if limit is not None and limit <= 0:
            # Preserve the legacy immediate return, including for an unavailable file.
            return {"records": [], "count": 0, "available": True}
        excluded = tuple(exclude_message_ids)
        path = self._path(scope)
        # Keep only the base query and latest filtered query per path. A cheap
        # activity lookup must not evict the larger shared-history tail.
        cache_key = (path, bool(excluded))
        with self._snapshot_lock:
            try:
                stat = os.stat(path)
                stamp = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
                cached = self._snapshot_cache.get(cache_key)
                # A different selection or larger requested tail needs a full scan;
                # no new retention limit may silently discard requested evidence.
                reusable = (cached is not None and cached["scope"] == str(scope)
                            and cached["excluded"] == excluded
                            and (cached["limit"] is None or limit is not None
                                 and cached["limit"] >= limit))
                if reusable and cached["stamp"] == stamp:
                    result = cached["result"]
                else:
                    append = (reusable and cached["stamp"][:2] == stamp[:2]
                              and stamp[2] > cached["stamp"][2]
                              and stamp[3] > cached["stamp"][3])
                    capacity = cached["limit"] if append else limit
                    state = cached["committed"] if append else (0, [], 0)
                    with open(path, "rb") as handle:
                        handle.seek(state[0])
                        result, committed = self._scan_snapshot(
                            handle, scope, capacity, excluded, state)
                        after = os.fstat(handle.fileno())
                    # Concurrent file changes cannot anchor the next incremental read.
                    after_stamp = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                    if after_stamp == stamp:
                        self._snapshot_cache[cache_key] = {"stamp": stamp, "scope": str(scope), "excluded": excluded,
                            "limit": capacity, "result": result, "committed": committed}
                    else:
                        self._snapshot_cache.pop(cache_key, None)
                detached = deepcopy(result)
                if limit is not None:
                    detached["records"] = detached["records"][-limit:]
                return detached
            except (OSError, UnicodeError):
                # Partial/failed reads must neither publish nor retain partial counts.
                self._snapshot_cache.pop(cache_key, None)
                return {"records": [], "count": 0, "available": False}

    def _scan_snapshot(self, handle, scope, limit, excluded, state):
        """Resume after complete lines; keep an unterminated final row tentative.

        Recorder archives are append-only. Replacements, shrinkage and same-size
        rewrites rebuild in the caller; a valid final row without newline remains
        visible now but is reread if later bytes complete or invalidate that row.
        """
        offset, prior, count = state
        records = deque(prior, maxlen=limit)
        committed = None
        for raw, end_offset in self._snapshot_lines(handle):
            if not raw.endswith((b"\n", b"\r")):
                # Retain only one tail copy, not one per complete archive row.
                committed = (offset, list(records), count)
            # Decode before JSON validation: invalid UTF-8 has always failed the
            # whole snapshot rather than behaving like an ignorable malformed row.
            text = raw.decode("utf-8")
            try:
                item = json.loads(text)
            except (ValueError, TypeError):
                item = None
            if (isinstance(item, dict) and isinstance(item.get("line"), str)
                    and item["line"]):
                metadata = item.get("record")
                if not (isinstance(metadata, dict) and "id" in metadata
                        and any(metadata["id"] == message_id for message_id in excluded)):
                    count += 1
                    row = {"scope": str(scope), "ts": item.get("ts"), "line": item["line"]}
                    if "record" in item:
                        row["record"] = metadata
                    records.append(row)
            if raw.endswith((b"\n", b"\r")):
                # Snapshot only at a complete-line boundary, including malformed
                # rows, so append replay never double-counts the tentative last row.
                offset = end_offset
        if committed is None:
            committed = (offset, list(records), count)
        return {"records": list(records), "count": count, "available": True}, committed

    @staticmethod
    def _snapshot_lines(handle):
        """Yield universal-newline byte records and exact resumable file offsets.

        Binary offsets avoid text-reader cookies; splitting CR as well as LF
        preserves the old UTF-8 text reader's universal-newline acceptance.
        """
        position = handle.tell()
        for block in handle:
            for match in re.finditer(rb"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$", block):
                raw = match.group()
                position += len(raw)
                yield raw, position

    def search(self, keyword: str, scope: str = "", limit: int = 10) -> list:
        """按关键词找记录（跨 scope 返回 [{scope, line}]）。"""
        key = str(keyword or "").strip()
        if not key:
            return []
        out: list = []
        try:
            names = ([self._path(scope)] if scope else
                     [os.path.join(self.root, n) for n in os.listdir(self.root)
                      if n.endswith(".jsonl")])
            for path in names[:MAX_SCOPES]:
                if not os.path.exists(path):
                    continue
                with open(path, encoding="utf-8") as handle:
                    for raw in handle:
                        try:
                            item = json.loads(raw)
                        except Exception:                  # noqa: BLE001
                            continue
                        line = str(item.get("line") or "")
                        if key in line:
                            out.append({"scope": os.path.basename(path)[:-6],
                                        "ts": item.get("ts"), "line": line[:300]})
            out.sort(key=lambda x: x.get("ts") or 0, reverse=True)
            return out[:limit]
        except Exception:                                  # noqa: BLE001
            return []
