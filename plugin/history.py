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


class Recorder:
    """按 scope 存聊天记录（JSONL，一行一条）。"""

    def __init__(self, root: str | None = None) -> None:
        """Initialize the explicit archive or configured data root, never another account's path."""
        root = str(data_root() / "history") if root is None else root
        self.root = root
        # One detached tail/count per file; the lock also protects future threaded readers.
        self._snapshot_lock = RLock()
        self._snapshot_cache = {}
        try:
            os.makedirs(root, exist_ok=True)
        except Exception:                                 # noqa: BLE001
            pass

    def _path(self, scope: str) -> str:
        """scope → 文件名（把 : 等非法字符换掉）。"""
        safe = re.sub(r"[^0-9A-Za-z_.-]", "_", str(scope or "default"))[:60]
        return os.path.join(self.root, safe + ".jsonl")

    def append(self, scope: str, line: str, *, record: dict | None = None) -> None:
        """Append compatible text plus optional untouched source metadata, without inferring identities."""
        try:
            text = str(line or "").strip()
            if not text:
                return
            item = {"ts": int(time.time()), "line": text[:2000]}
            if record is not None:
                # Freeze native event metadata; never derive identity/authority from text.
                item["record"] = deepcopy(record)
            encoded = json.dumps(item, ensure_ascii=False)
            with open(self._path(scope), "a", encoding="utf-8") as handle:
                handle.write(encoded + "\n")
        except Exception:                                  # noqa: BLE001
            pass

    def load(self, scope: str, limit: int = 200) -> list:
        """读最近 limit 行（旧 → 新）。"""
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
            return [x for x in lines if x][-limit:]
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
