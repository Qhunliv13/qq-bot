# -*- coding: utf-8 -*-
"""彼岸酱的记忆内核（2026-10-19）。

三层记忆 + 自动压缩（对应 docs/QQBOT_CAPABILITY_PLAN.md 与 MULTI_SCOPE 设计）：

    L1 短期：调用方传入的最近消息（本模块不持有）
    L2 摘要：按 scope 一份 + 一份全局摘要（本模块负责读写与压缩）
    L3 长期：全局事实片段（带 source_scope，便于追溯）

scope 约定（单一实现，别在别处再拼）：
    "group:<群号>" / "private:<QQ>" / 其它 "<platform>:<id>"

设计约束：
    · 压缩**只写 L2**，绝不改写用户原话（原话在 L1，由调用方保管）
    · 本模块**不调用模型**：压缩动作由调用方把 `compress_messages()` 的结果交给模型，
      再把模型输出写回 `set_summary()` —— 保持本模块可单测、无网络、无依赖 ✓
    · 全部原子写盘（tmp + replace）✓ 读失败一律返回安全默认 ✓
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Iterable, Optional

#: 每条摘要的长度上限（字符）：防止摘要膨胀成第二份全文
MAX_SUMMARY_CHARS = 100000
#: L3 最多保留多少条（超出按重要度/时间淘汰）
MAX_FACTS = 2000


def _fact_scope(item: dict) -> str:
    """Resolve only explicit scope evidence; never derive it from identity or text."""
    provenance = item.get("source")
    scope = provenance.get("scope") if isinstance(provenance, dict) else None
    return str(scope if scope is not None else item.get("source_scope") or "")


class Memory:
    """按 scope 组织的小型记忆库（JSON 落盘）。"""

    def __init__(self, path: str) -> None:
        """绑定一个 JSON 文件路径（目录不存在会自动创建）。"""
        self.path = path
        self.data: dict = {"version": 1, "global": {"summary": "", "facts": []},
                           "scopes": {}}

    # ---------- 读写 ----------
    def load(self) -> "Memory":
        """从磁盘载入（缺失/损坏时保持空记忆，绝不抛）。"""
        try:
            with open(self.path, encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict) and "scopes" in loaded:
                self.data = loaded
        except Exception:                                # noqa: BLE001
            pass
        return self

    def save(self) -> None:
        """原子写盘（tmp + replace），失败不抛。"""
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path) or ".")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception:                                # noqa: BLE001
            pass

    # ---------- L2 摘要 ----------
    def _scope(self, scope: str) -> dict:
        """取（必要时创建）某 scope 的记忆块。"""
        scopes = self.data.setdefault("scopes", {})
        item = scopes.setdefault(scope, {"summary": "", "updated_at": 0, "turns": 0})
        return item

    def get_summary(self, scope: str) -> str:
        """取某 scope 的摘要（没有则为空串）。"""
        return str(self._scope(scope).get("summary") or "")

    def get_global_summary(self) -> str:
        """取全局摘要（跨场景共享的"她"的连续性）。"""
        return str((self.data.get("global") or {}).get("summary") or "")

    def set_summary(self, scope: str, summary: str, *, global_too: bool = False) -> None:
        """写入摘要（截断到上限 ✓ 幂等 ✓）。

        Args:
            scope: 作用域（"group:123" / "private:456"）。
            summary: 模型产出的摘要文本。
            global_too: 是否同时更新全局摘要（低频调用 ✓）。
        """
        text = (summary or "").strip()[:MAX_SUMMARY_CHARS]
        item = self._scope(scope)
        item["summary"] = text
        item["updated_at"] = int(time.time())
        item["turns"] = int(item.get("turns") or 0) + 1
        if global_too:
            # [shared-memory-v2] 合并而不是覆盖（每群各写一份 ⇒ 保留最近几段 ✓）
            g = self.data.setdefault("global", {})
            old = str(g.get("summary") or "").strip()
            merged = (old + "\n" + text).strip() if old and text not in old else (old or text)
            lines = [ln for ln in merged.splitlines() if ln.strip()]
            keep = lines[-12:]
            g["summary"] = "\n".join(keep)[-100000:]
            g["updated_at"] = int(time.time())
        self.save()

    def bump_turns(self, scope: str) -> int:
        """累加该 scope 的消息计数（用于触发压缩 ✓）。"""
        item = self._scope(scope)
        item["turns"] = int(item.get("turns") or 0) + 1
        return item["turns"]

    # ---------- L3 长期事实 ----------
    def _next_fact_id(self) -> str:
        """生成简短事实 id（f001 起递增，便于她口述删除某条）。"""
        bucket = self.data.setdefault("global", {}).setdefault("facts", [])
        used = {str(it.get("id") or "") for it in bucket}
        i = 1
        while "f%03d" % i in used:
            i += 1
        return "f%03d" % i

    def facts_for_scope(self, current_scope: str, *, muted=()) -> list[dict]:
        """Return detached visible facts; unknown legacy labels remain grandfathered.

        Provenance is evidence, never an owner role or permission grant. A known
        private first origin stays private even if another scope repeats its text.
        """
        from .cross_scope_memory import visible_scope

        bucket = (self.data.get("global") or {}).get("facts") or []
        out = []
        for item in bucket:
            scope = _fact_scope(item)
            # Unknown legacy labels stay readable, without fabricating provenance.
            # Known scope namespaces must pass the shared visibility policy.
            known = scope.isascii() and scope.isdigit() or scope.startswith(("private:", "group:", "private_"))
            if known and not visible_scope(scope, current_scope, muted):
                continue
            out.append(json.loads(json.dumps(item, ensure_ascii=False, allow_nan=False)))
        return out

    def list_facts(self, limit: Optional[int] = 500, *, current_scope=None,
                   muted=()) -> list[dict]:
        """List detached facts; None removes only the historical 500-row view cap.

        No current_scope preserves the legacy global view. The source string is
        kept for callers; provenance separately exposes the actual source record.
        """
        bucket = ((self.data.get("global") or {}).get("facts") or []
                  if current_scope is None else self.facts_for_scope(current_scope, muted=muted))
        out = []
        for it in bucket[:limit]:
            row = {"id": it.get("id") or "", "text": it.get("text") or "",
                   "at": it.get("at") or 0, "source": it.get("source_scope") or "",
                   "source_scope": it.get("source_scope") or ""}
            if "source" in it:
                row["provenance"] = json.loads(json.dumps(it["source"], ensure_ascii=False,
                                                          allow_nan=False))
            out.append(row)
        return out

    def edit_fact(self, fact_id: str, text: str) -> bool:
        """按 id 改一条事实（返回是否找到）。"""
        bucket = (self.data.get("global") or {}).get("facts") or []
        for it in bucket:
            if str(it.get("id") or "") == str(fact_id):
                it["text"] = (text or "").strip()
                it["at"] = int(time.time())
                self.save()
                return True
        return False

    def delete_fact(self, fact_id: str = "", keyword: str = "") -> int:
        """按 id 或关键词删事实，返回删除条数。"""
        bucket = (self.data.get("global") or {}).get("facts") or []
        keep, removed = [], 0
        for it in bucket:
            hit_id = fact_id and str(it.get("id") or "") == str(fact_id)
            hit_kw = keyword and keyword in str(it.get("text") or "")
            if hit_id or hit_kw:
                removed += 1
                continue
            keep.append(it)
        if removed:
            self.data.setdefault("global", {})["facts"] = keep
            self.save()
        return removed

    def add_fact(self, text: str, source_scope: str = "", *, source=None) -> str:
        """Store a fact and optional detached JSON provenance, never inferred trust.

        Text deduplication preserves the first source, including legacy labels;
        repeating private text in a public group must not publish its provenance.
        Invalid source data fails before any in-memory or on-disk mutation.
        """
        if source is not None and not isinstance(source, dict):
            raise TypeError("source must be a dictionary or None")
        snapshot = (json.loads(json.dumps(source, ensure_ascii=False, allow_nan=False))
                    if source is not None else None)
        clean = (text or "").strip()
        if not clean:
            return
        bucket = self.data.setdefault("global", {}).setdefault("facts", [])
        for item in bucket:
            if not item.get("id"):
                item["id"] = self._next_fact_id()
            if clean == str(item.get("text") or ""):
                return str(item.get("id") or "")
        fid = self._next_fact_id()
        fact = {"id": fid, "text": clean, "source_scope": source_scope,
                "at": int(time.time())}
        if snapshot is not None:
            fact["source"] = snapshot
        bucket.append(fact)
        if len(bucket) > MAX_FACTS:
            del bucket[0:len(bucket) - MAX_FACTS]
        self.save()
        return fid

    def search_facts(self, query: str, limit: int = 500, *,
                     allowed_scopes=None) -> list[str]:
        """Keep historical OR scoring over all stored facts, optionally exact scopes.

        allowed_scopes is a caller-supplied retrieval filter, not an authorization
        derived from provenance. Main's AND search can instead filter list_facts(None).
        """
        words = [w for w in str(query or "").replace("，", " ").split() if w]
        bucket = (self.data.get("global") or {}).get("facts") or []
        allowed = None if allowed_scopes is None else {str(scope) for scope in allowed_scopes}
        scored = []
        for item in bucket:
            scope = _fact_scope(item)
            if allowed is not None and scope not in allowed:
                continue
            text = str(item.get("text") or "")
            score = sum(1 for w in words if w and w in text)
            if score:
                scored.append((score, int(item.get("at") or 0), text))
        scored.sort(reverse=True)
        return [t for _s, _a, t in scored[:limit]]

    # ---------- 自动压缩（模型侧动作由调用方执行） ----------
    def compress_messages(self, scope: str, recent: Iterable[str],
                          max_chars: int = 600000) -> Optional[list[dict]]:
        """构造"压缩请求"的 messages；返回 None 表示当前无需压缩 ✓。

        调用方拿到后交给模型，再把输出用 `set_summary(scope, out)` 写回，
        并自行把 recent 里的旧消息丢弃（保留锚点 ✓）。

        Args:
            scope: 作用域。
            recent: 该 scope 的最近消息（"昵称: 内容"）。
            max_chars: 超过该字符数才需要压缩（默认 4000 ≈ 2k token）。

        Returns:
            Optional[list[dict]]: OpenAI 兼容 messages；无需压缩时 None。
        """
        lines = list(recent)
        if sum(len(x) for x in lines) <= max_chars:
            return None
        old_summary = self.get_summary(scope) or "（暂无摘要）"
        body = "\n".join(lines)
        prompt = (
            "把下面的群/私聊记录压缩成一段不超过 %d 字的摘要，供你之后继续对话时回忆。\n"
            "要求：\n"
            "1) 保留：谁是谁（昵称与关系）、在聊什么、已得出的结论、承诺过的事、情绪基调。\n"
            "2) 丢弃：寒暄、重复、与后续无关的枝节、表情包描述。\n"
            "3) 只输出摘要正文，不要前言、不要列表编号。\n\n"
            "【已有摘要】\n%s\n\n【待压缩记录】\n%s" % (MAX_SUMMARY_CHARS, old_summary, body)
        )
        return [{"role": "user", "content": prompt}]


if __name__ == "__main__":
    # 自检（不依赖 pytest，可直接 python3 memory.py 跑 ✓）
    mem = Memory("/tmp/_higan_mem_selftest.json").load()
    mem.set_summary("group:1", "群 1 里大家在聊显卡价格")
    assert "显卡" in mem.get_summary("group:1")
    assert mem.get_summary("private:9") == ""            # scope 隔离 ✓
    mem.add_fact("小七是群友，做二手显卡", "private:9")
    mem.add_fact("小七是群友，做二手显卡", "private:9")   # 去重 ✓
    assert len(mem.search_facts("小七 显卡")) == 1
    assert mem.compress_messages("group:1", ["a" * 5000]) is not None
    assert mem.compress_messages("group:1", ["短"]) is None
    print("memory.py 自检通过 ✓")


class WakePersonFacts:
    """关于「唤醒人」的独立记录（最多 200 条/人，不占用 global.facts 配额）。"""

    MAX_PER_PERSON = 200

    def __init__(self, path: str) -> None:
        """绑定 JSON 文件。"""
        self.path = path
        self.data: dict = {"version": 1, "persons": {}}

    def load(self) -> "WakePersonFacts":
        """读盘。"""
        try:
            with open(self.path, encoding="utf-8") as handle:
                raw = json.load(handle)
            if isinstance(raw, dict):
                self.data = raw
                self.data.setdefault("persons", {})
        except Exception:                                  # noqa: BLE001
            pass
        return self

    def save(self) -> None:
        """落盘。"""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(self.data, handle, ensure_ascii=False, indent=2)

    def _bucket(self, qq: str) -> list:
        """取某人的条目列表。"""
        return self.data.setdefault("persons", {}).setdefault(str(qq), [])

    def add(self, qq: str, text: str) -> str:
        """加一条（去重、超 200 淘汰最旧）。"""
        qq = str(qq or "").strip()
        clean = (text or "").strip()
        if not qq or not clean:
            return ""
        bucket = self._bucket(qq)
        for item in bucket:
            if item.get("text") == clean:
                return str(item.get("id") or "")
        fid = "w%03d" % (len(bucket) + 1)
        while any(i.get("id") == fid for i in bucket):
            fid = "w%03d" % (int(fid[1:]) + 1)
        bucket.append({"id": fid, "text": clean, "at": int(time.time())})
        if len(bucket) > self.MAX_PER_PERSON:
            del bucket[0:len(bucket) - self.MAX_PER_PERSON]
        self.save()
        return fid

    def list(self, qq: str) -> list:
        """列某人的记录。"""
        return [dict(i) for i in self._bucket(str(qq))]

    def search(self, qq: str, query: str) -> list:
        """按关键词找。"""
        q = str(query or "").strip()
        return [dict(i) for i in self._bucket(str(qq))
                if not q or q in str(i.get("text") or "")]

    def delete(self, qq: str, fid: str = "", keyword: str = "") -> int:
        """按编号或关键词删，返回删除条数。"""
        bucket = self._bucket(str(qq))
        keep, removed = [], 0
        for item in bucket:
            if (fid and item.get("id") == fid) or (keyword and keyword in str(item.get("text") or "")):
                removed += 1
                continue
            keep.append(item)
        if removed:
            self.data["persons"][str(qq)] = keep
            self.save()
        return removed

    def count(self, qq: str) -> int:
        """某人有几条记录。"""
        return len(self._bucket(str(qq)))
