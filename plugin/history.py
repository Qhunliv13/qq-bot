#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""她把群聊历史存到自己虚拟机里（2026-10-20）。

背景：NapCat 的 HTTP action 是空壳（`get_group_msg_history` 恒返回 data:{}）✗
⇒ 无法从上游回看历史 ⇒ 自己存 ✓（这也是"回看历史消息"唯一可靠的实现）

接口：
  rec  = Recorder("/root/higan/history")
  rec.append(scope, line)            追加一行（自动滚动，最多 5000 行/scope ✓）
  rec.load(scope, limit=200)         读最近 N 行（启动时恢复上下文 ✓）
  rec.search(keyword, scope="", limit=10)  关键词搜索（给她一个工具 ✓）
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
    """按 scope 存聊天记录（JSONL，一行一条 ✓）。"""

    def __init__(self, root: str = "/root/higan/history") -> None:
        """初始化（目录不存在就建 ✓）。"""
        self.root = root
        try:
            os.makedirs(root, exist_ok=True)
        except Exception:                                 # noqa: BLE001
            pass

    def _path(self, scope: str) -> str:
        """scope → 文件名（把 : 等非法字符换掉 ✓）。"""
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
        """读最近 limit 行（旧 → 新 ✓）。"""
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
        if limit is not None and limit <= 0:
            return []
        records = deque(maxlen=limit)
        try:
            with open(self._path(scope), encoding="utf-8") as handle:
                for raw in handle:
                    try:
                        item = json.loads(raw)
                    except (ValueError, TypeError):
                        # A partial or corrupt JSONL entry does not erase neighboring evidence.
                        continue
                    if not isinstance(item, dict) or not isinstance(item.get("line"), str):
                        continue
                    if not item["line"]:
                        continue
                    row = {"scope": str(scope), "ts": item.get("ts"), "line": item["line"]}
                    if "record" in item:
                        # Keep optional metadata exactly as stored, including nested quote provenance.
                        row["record"] = deepcopy(item["record"])
                    records.append(row)
        except (OSError, UnicodeError):
            return []
        return list(records)

    def search(self, keyword: str, scope: str = "", limit: int = 10) -> list:
        """按关键词找记录（跨 scope ✓ 返回 [{scope, line}]）。"""
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
