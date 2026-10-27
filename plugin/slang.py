#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""俚语与黑话的持久学习、查询和最近使用视图。

存储：/root/higan/slang.json
结构：{"words": {"词": {"meaning": "...", "source": "...", "count": N, "ts": 时间}}}
按调用方提供的文件路径共享词库。
用法：
  s = Slang("/root/higan/slang.json").load()
  s.learn("红温", "被人气到脸红", "群聊")
  s.lookup("红温")   # -> "被人气到脸红"（没学过返回 ""）
  s.recent(20)       # -> [{word, meaning, count}]（按最近使用排序）
"""

from __future__ import annotations

import json
import os
import tempfile
import time

MAX_WORDS = 800


class Slang:
    """俚语/黑话学习与查询。"""

    def __init__(self, path: str) -> None:
        """初始化（不读盘）。"""
        self.path = path
        self.data: dict = {"words": {}}

    def load(self) -> "Slang":
        """读盘（失败保持空库）。"""
        try:
            with open(self.path, encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict) and isinstance(loaded.get("words"), dict):
                self.data = loaded
        except Exception:                                 # noqa: BLE001
            pass
        return self

    def save(self) -> None:
        """原子写盘。"""
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path) or ".")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except Exception:                                 # noqa: BLE001
            pass

    def learn(self, word: str, meaning: str, source: str = "") -> bool:
        """学一个词（词义去重、次数累加）。没有词或意思就不学。"""
        key = str(word or "").strip()[:24]
        val = str(meaning or "").strip()[:200]
        if not key or not val:
            return False
        words = self.data.setdefault("words", {})
        item = words.setdefault(key, {"meaning": val, "source": "",
                                     "count": 0, "ts": 0})
        if val not in str(item.get("meaning") or ""):
            item["meaning"] = ((item.get("meaning") or "") + ("；" if item.get("meaning") else "") + val)[:300]
        item["source"] = str(source or item.get("source") or "")[:40]
        item["count"] = int(item.get("count") or 0) + 1
        item["ts"] = int(time.time())
        if len(words) > MAX_WORDS:                        # 只留最近用过的
            order = sorted(words.items(), key=lambda kv: int(kv[1].get("ts") or 0), reverse=True)
            self.data["words"] = dict(order[:MAX_WORDS])
        self.save()
        return True

    def lookup(self, word: str) -> str:
        """查询已存词义；没学过时返回空串。"""
        key = str(word or "").strip()
        item = (self.data.get("words") or {}).get(key) or {}
        return str(item.get("meaning") or "")

    def recent(self, limit: int = 20) -> list:
        """最近学/用的词（按时间倒序）。"""
        items = list((self.data.get("words") or {}).items())
        items.sort(key=lambda kv: int(kv[1].get("ts") or 0), reverse=True)
        out = []
        for key, val in items[: max(1, min(200, int(limit or 20)))]:
            out.append({"word": key, "meaning": str(val.get("meaning") or "")[:120],
                        "count": int(val.get("count") or 0)})
        return out
