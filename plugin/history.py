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

MAX_LINES = 5000
MAX_SCOPES = 30


class Recorder:
    """按 scope 存聊天记录（JSONL，一行一条）。"""

    def __init__(self, root: str = "/root/higan/history") -> None:
        """初始化（目录不存在就建）。"""
        self.root = root
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
        records = deque(maxlen=limit)
        count = 0
        try:
            with open(self._path(scope), encoding="utf-8") as handle:
                # Read the entire file: a bounded tail alone cannot provide the
                # stable eligible count used to align globally cached prefixes.
                for raw in handle:
                    try:
                        item = json.loads(raw)
                    except (ValueError, TypeError):
                        # A partial or corrupt JSONL entry does not erase neighboring evidence.
                        continue
                    if not isinstance(item, dict) or not isinstance(item.get("line"), str):
                        # Keep precisely the legacy valid-record acceptance contract.
                        continue
                    if not item["line"]:
                        continue
                    metadata = item.get("record")
                    if (isinstance(metadata, dict) and "id" in metadata
                            and any(metadata["id"] == message_id for message_id in excluded)):
                        # Scope is already bound by the file; retain native ID equality
                        # (not string normalization) and never inspect legacy text.
                        continue
                    count += 1
                    row = {"scope": str(scope), "ts": item.get("ts"), "line": item["line"]}
                    if "record" in item:
                        # json.loads owns this fresh tree; retain it directly so evicted
                        # rows need no deepcopy, while each returned scan stays detached.
                        row["record"] = metadata
                    records.append(row)
        except (OSError, UnicodeError):
            # Partial reads are not coherent snapshots; never publish their tail/count.
            return {"records": [], "count": 0, "available": False}
        return {"records": list(records), "count": count, "available": True}

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
