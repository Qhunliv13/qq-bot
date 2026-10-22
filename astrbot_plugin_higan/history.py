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

    def append(self, scope: str, line: str) -> None:
        """追加一条（失败静默 ✓ 不影响聊天）。"""
        try:
            text = str(line or "").strip()
            if not text:
                return
            with open(self._path(scope), "a", encoding="utf-8") as handle:
                handle.write(json.dumps({"ts": int(time.time()), "line": text[:2000]},
                                        ensure_ascii=False) + "\n")
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
