# -*- coding: utf-8 -*-
"""摘要、长期事实与唤醒人记录的本地存储接口。

Memory 读写按 scope 的摘要和全局事实，保留显式来源证据；可见性过滤不从
文字推导身份或权限，未知旧来源标签保持兼容。WakePersonFacts 独立保存
按人的记录，不占用 global.facts 配额。

compress_messages() 是可选摘要请求构造接口，不调用模型、不修改原消息；
调用方自行决定是否调用、是否写回摘要，不意味着主流程自动压缩。
Memory.save() 使用临时文件替换写盘，WakePersonFacts.save() 直接写 JSON。
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import time
from typing import Iterable, Optional

# Preserve the existing direct-script self-test as well as normal package imports.
if __package__:
    from .fact_images import images_metadata, normalize_images
    from .fact_structure import normalize_structure
else:
    from fact_images import images_metadata, normalize_images
    from fact_structure import normalize_structure

#: 每条摘要的长度上限（字符）：防止摘要膨胀成第二份全文
MAX_SUMMARY_CHARS = 100000
#: 历史容量阈值，仅保留兼容导入；不再静默淘汰用户的长期记录。
MAX_FACTS = 2000


def _fact_scope(item: dict) -> str:
    """Resolve only explicit scope evidence; never derive it from identity or text."""
    provenance = item.get("source")
    scope = provenance.get("scope") if isinstance(provenance, dict) else None
    return str(scope if scope is not None else item.get("source_scope") or "")


def _dedup_scope(item: dict) -> str:
    """Partition exact private namespaces without inventing filename/identity aliases.

    Public and unknown legacy sources retain their established shared dedup class;
    native and legacy-private labels remain isolated by their exact stored scope.
    """
    scope = _fact_scope(item)
    return scope if scope.startswith(("private:", "private_")) else ""


def _categories(value) -> list[str]:
    """Validate detached ordinary labels without coercion or authority semantics."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError("categories must be an array of strings")
    result = []
    for label in value:
        if not isinstance(label, str) or not label.strip():
            raise ValueError("categories must contain nonempty strings")
        clean = label.strip()
        if clean not in result:
            result.append(clean)
    return result


class Memory:
    """按 scope 组织的小型记忆库（JSON 落盘）。"""

    def __init__(self, path: str) -> None:
        """绑定 JSON 文件路径；保存时创建所需目录。"""
        self.path = path
        self.data: dict = {"version": 1, "global": {"summary": "", "facts": []},
                           "scopes": {}}

    # ---------- 读写 ----------
    def load(self) -> "Memory":
        """从磁盘载入；缺失或读取失败时保留当前内存数据，不抛异常。"""
        try:
            with open(self.path, encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict) and "scopes" in loaded:
                self.data = loaded
        except Exception:                                # noqa: BLE001
            pass
        return self

    def save_checked(self) -> None:
        """Persist atomically or raise; callers must not report failed writes as success."""
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, ensure_ascii=False, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        finally:
            # A failed replacement must not accumulate abandoned memory snapshots.
            if os.path.exists(tmp):
                os.unlink(tmp)

    def save(self) -> None:
        """Keep the legacy best-effort interface; checked mutations use save_checked."""
        try:
            self.save_checked()
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
        """写入摘要并更新计数与时间，按字符上限截断。

        Args:
            scope: 作用域（"group:123" / "private:456"）。
            summary: 模型产出的摘要文本。
            global_too: 是否同时合并到全局摘要。
        """
        text = (summary or "").strip()[:MAX_SUMMARY_CHARS]
        item = self._scope(scope)
        item["summary"] = text
        item["updated_at"] = int(time.time())
        item["turns"] = int(item.get("turns") or 0) + 1
        if global_too:
            # 合并全局摘要，保留最后 12 个非空行并应用字符上限。
            g = self.data.setdefault("global", {})
            old = str(g.get("summary") or "").strip()
            merged = (old + "\n" + text).strip() if old and text not in old else (old or text)
            lines = [ln for ln in merged.splitlines() if ln.strip()]
            keep = lines[-12:]
            g["summary"] = "\n".join(keep)[-100000:]
            g["updated_at"] = int(time.time())
        self.save()

    def bump_turns(self, scope: str) -> int:
        """累加该 scope 的消息计数（用于触发压缩）。"""
        item = self._scope(scope)
        item["turns"] = int(item.get("turns") or 0) + 1
        return item["turns"]

    # ---------- L3 长期事实 ----------
    def _next_fact_id(self, *, allocate: bool = True) -> str:
        """Reserve monotonic IDs, or preserve the legacy high-water mark before deletion."""
        global_data = self.data.setdefault("global", {})
        bucket = global_data.setdefault("facts", [])
        used = [int(str(it.get("id"))[1:]) for it in bucket
                if str(it.get("id") or "").startswith("f")
                and str(it.get("id"))[1:].isascii()
                and str(it.get("id"))[1:].isdigit()]
        # Persist a high-water mark so replaying a deletion cannot hit a new fact.
        previous = global_data.get("next_fact_id", 1)
        previous = previous if isinstance(previous, int) and not isinstance(previous, bool) else 1
        number = max(previous, max(used, default=0) + 1)
        global_data["next_fact_id"] = number + int(allocate)
        return "f%03d" % number

    def facts_for_scope(self, current_scope: str, *, muted=()) -> list[dict]:
        """Return detached visible facts; unknown legacy labels remain grandfathered.

        Provenance and structure are evidence, never owner roles or permissions.
        Private first origins stay private; public repeats get independent records.
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
            detached = json.loads(json.dumps(item, ensure_ascii=False, allow_nan=False))
            # Read projections expose legacy defaults without backfilling durable data.
            detached["structure"] = normalize_structure(item.get("structure"))
            out.append(detached)
        return out

    def list_facts(self, limit: Optional[int] = 500, *, current_scope=None,
                   muted=()) -> list[dict]:
        """List detached facts; None returns all stored facts in the selected view.

        No current_scope preserves the legacy global view. The source string is
        kept for callers; provenance separately exposes the actual source record.
        """
        bucket = ((self.data.get("global") or {}).get("facts") or []
                  if current_scope is None else self.facts_for_scope(current_scope, muted=muted))
        out = []
        for it in bucket[:limit]:
            row = {"id": it.get("id") or "", "text": it.get("text") or "",
                   "at": it.get("at") or 0, "source": it.get("source_scope") or "",
                   "source_scope": it.get("source_scope") or "",
                   "categories": _categories(it.get("categories")),
                   "structure": normalize_structure(it.get("structure")),
                   "images": images_metadata(it.get("images"))}
            if "source" in it:
                row["provenance"] = json.loads(json.dumps(it["source"], ensure_ascii=False,
                                                          allow_nan=False))
            out.append(row)
        return out

    def category_counts(self, *, current_scope=None, muted=()) -> dict:
        """Count labels only in the caller's visible view, including uncategorized facts."""
        rows = self.list_facts(None, current_scope=current_scope, muted=muted)
        counts = {}
        for row in rows:
            for label in row["categories"]:
                counts[label] = counts.get(label, 0) + 1
        return {"categories": [{"category": label, "count": counts[label]}
                               for label in sorted(counts)],
                "total": len(rows),
                "uncategorized": sum(not row["categories"] for row in rows)}

    def classify_facts(self, fact_ids, categories, *, current_scope=None, muted=()) -> dict:
        """Replace labels atomically after validating every ID against the visible view."""
        if not isinstance(fact_ids, list) or not fact_ids:
            raise ValueError("ids must be a nonempty array of strings")
        if any(not isinstance(fid, str) or not fid.strip() for fid in fact_ids):
            raise ValueError("ids must contain nonempty strings")
        if categories is None:
            raise TypeError("categories must be an array of strings")
        ids = list(dict.fromkeys(fid.strip() for fid in fact_ids))
        labels = _categories(categories)
        visible = {row["id"] for row in self.list_facts(
            None, current_scope=current_scope, muted=muted)}
        missing = [fid for fid in ids if fid not in visible]
        if missing:
            # Hidden and nonexistent IDs are deliberately indistinguishable.
            return {"ok": False, "updated": 0, "missing": missing}
        previous = copy.deepcopy(self.data)
        updated = 0
        for item in (self.data.get("global") or {}).get("facts") or []:
            if item.get("id") in ids and _categories(item.get("categories")) != labels:
                item["categories"] = list(labels)
                updated += 1
        try:
            if updated:
                self.save_checked()
        except Exception:
            # Preserve in-memory and durable agreement if the atomic write fails.
            self.data = previous
            raise
        return {"ok": True, "updated": updated, "ids": ids,
                "categories": list(labels), "missing": []}

    def get_fact_image(self, fact_id: str, index: int, *, current_scope=None,
                       muted=()) -> Optional[dict]:
        """Read one visible image; hidden facts and missing zero-based indices return None."""
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValueError("image index must be a nonnegative integer")
        bucket = ((self.data.get("global") or {}).get("facts") or []
                  if current_scope is None else self.facts_for_scope(current_scope, muted=muted))
        for item in bucket:
            if str(item.get("id") or "") == str(fact_id):
                refs = normalize_images(item.get("images"))
                if index >= len(refs):
                    return None
                metadata = images_metadata(refs)[index]
                return {"ref": refs[index], **metadata}
        return None

    def edit_fact(self, fact_id: str, text: str, *, images=None, structure=None,
                  current_scope=None, muted=()) -> bool:
        """Atomically edit visible facts, preserving omitted images and structure.

        An explicit empty list clears attachments, but a fact must retain text or
        images. Keep the existing edit timestamp convention and original provenance.
        """
        normalized = normalize_images(images) if images is not None else None
        metadata = normalize_structure(structure) if structure is not None else None
        clean = (text or "").strip()
        visible = {str(row["id"]) for row in self.list_facts(
            None, current_scope=current_scope, muted=muted)}
        if str(fact_id) not in visible:
            return False
        bucket = (self.data.get("global") or {}).get("facts") or []
        for item in bucket:
            if str(item.get("id") or "") == str(fact_id):
                retained = (normalize_images(item.get("images"))
                            if normalized is None else normalized)
                if not clean and not retained:
                    raise ValueError("a fact must contain text or images")
                previous = copy.deepcopy(self.data)
                item["text"] = clean
                if normalized is not None:
                    item["images"] = retained
                if metadata is not None:
                    # Explicit replacement/reset cannot alter provenance or visibility.
                    item["structure"] = metadata
                item["at"] = int(time.time())
                try:
                    self.save_checked()
                except Exception:
                    # A failed write must leave the visible and durable fact unchanged.
                    self.data = previous
                    raise
                return True
        return False

    def delete_fact(self, fact_id: str = "", keyword: str = "", *,
                    current_scope=None, muted=(), checked: bool = False) -> int:
        """Delete visible facts; an explicit ID always wins over legacy keyword matching."""
        bucket = (self.data.get("global") or {}).get("facts") or []
        visible = {row["id"] for row in self.list_facts(
            None, current_scope=current_scope, muted=muted)}
        keep, removed = [], 0
        for it in bucket:
            hit = (str(it.get("id") or "") == fact_id if fact_id
                   else bool(keyword and keyword in str(it.get("text") or "")))
            if hit and it.get("id") in visible:
                removed += 1
                continue
            keep.append(it)
        if removed:
            previous = copy.deepcopy(self.data)
            # Capture the maximum before removing legacy rows lacking a persisted counter.
            self._next_fact_id(allocate=False)
            self.data.setdefault("global", {})["facts"] = keep
            try:
                if checked:
                    self.save_checked()
                else:
                    self.save()
            except Exception:
                self.data = previous
                raise
        return removed

    def add_fact(self, text: str, source_scope: str = "", *, source=None,
                 categories=None, images=None, structure=None) -> str:
        """Store detached provenance and persistent images with checked atomic writes.

        Text and canonical images deduplicate within the same privacy partition.
        Explicit structure also participates; omitted structure preserves legacy
        duplicate behavior. Each genuinely new item appends at the end with its
        normalized structure, even when its kind remains unspecified. Never
        replace the first origin, labels or ID, and never evict older records.
        Validate inputs before allocating IDs or changing data.
        """
        labels = _categories(categories)
        normalized = normalize_images(images)
        metadata = normalize_structure(structure) if structure is not None else None
        if source is not None and not isinstance(source, dict):
            raise TypeError("source must be a dictionary or None")
        snapshot = (json.loads(json.dumps(source, ensure_ascii=False, allow_nan=False))
                    if source is not None else None)
        clean = (text or "").strip()
        if not clean and not normalized:
            return
        bucket = (self.data.get("global") or {}).get("facts") or []
        # Resolve the duplicate before mutation so invalid legacy images cannot
        # partially allocate IDs or create a global container.
        partition = _dedup_scope({"source_scope": source_scope, "source": snapshot})
        duplicate = next((item for item in bucket
                          if partition == _dedup_scope(item)
                          and clean == str(item.get("text") or "")
                          and normalized == normalize_images(item.get("images"))
                          and (metadata is None
                               or metadata == normalize_structure(item.get("structure")))), None)
        if duplicate is not None and duplicate.get("id"):
            return str(duplicate["id"])
        previous = copy.deepcopy(self.data)
        try:
            bucket = self.data.setdefault("global", {}).setdefault("facts", [])
            # Preserve the established backfill convention for legacy missing IDs.
            for item in bucket:
                if not item.get("id"):
                    item["id"] = self._next_fact_id()
            if duplicate is not None:
                fid = str(duplicate["id"])
            else:
                fid = self._next_fact_id()
                fact = {"id": fid, "text": clean, "source_scope": source_scope,
                        "at": int(time.time()), "categories": labels,
                        "images": normalized}
                if snapshot is not None:
                    fact["source"] = snapshot
                # Every newly appended entry has a durable explicit shape even
                # when its meaning remains unspecified. Existing legacy entries
                # and deduplicated first origins are never rewritten.
                fact["structure"] = (metadata if metadata is not None
                                     else normalize_structure())
                # Facts are user records, not a cache: adding one must not erase another.
                bucket.append(fact)
            self.save_checked()
        except Exception:
            # Restore IDs, metadata and counters as well as the new record.
            self.data = previous
            raise
        return fid

    def search_facts(self, query: str, limit: int = 500, *,
                     allowed_scopes=None) -> list[str]:
        """Rank stored facts by OR keyword matches, optionally within exact scopes.

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

    # ---------- 可选摘要请求（模型侧动作由调用方执行） ----------
    def compress_messages(self, scope: str, recent: Iterable[str],
                          max_chars: int = 600000) -> Optional[list[dict]]:
        """构造"压缩请求"的 messages；返回 None 表示当前无需压缩。

        调用方拿到后交给模型，再把输出用 `set_summary(scope, out)` 写回，
        本方法不修改 recent，历史保留策略由调用方负责。

        Args:
            scope: 作用域。
            recent: 该 scope 的最近消息（"昵称: 内容"）。
            max_chars: 超过该字符数才构造摘要请求（默认 600000 字符）。

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
    # 自检（不依赖 pytest，可直接 python3 memory.py 跑）
    mem = Memory("/tmp/_higan_mem_selftest.json").load()
    mem.set_summary("group:1", "群 1 里大家在聊显卡价格")
    assert "显卡" in mem.get_summary("group:1")
    assert mem.get_summary("private:9") == ""            # scope 隔离
    mem.add_fact("小七是群友，做二手显卡", "private:9")
    mem.add_fact("小七是群友，做二手显卡", "private:9")   # 去重
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
