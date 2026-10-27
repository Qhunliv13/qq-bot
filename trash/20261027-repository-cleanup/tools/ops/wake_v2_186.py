#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""唤醒人/唤醒词重做 + 唤醒人专属事实（2026-10-25 用户要求）。

要求汇总：
  1) 事实上限 2000
  2) 查她有没有设唤醒人（瑞禾）
  3) 能列出全部唤醒人/唤醒词、能删
  4) 唤醒人/词要"主动检索"，不要直接读全量列表
     · 第一步只返回概览（按群给数量）
     · 第二步按群筛选看该群的名单
     · 另有 wake_search 按关键词查
  5) 设唤醒人必须用 QQ 号（名字不可信）
  6) 唤醒人有单独的"关于他的记录"（最多 200 条，不计入 facts 总数）
  7) 唤醒人的消息在上下文里带**结构化标记**（基于 QQ 号判定，不看昵称）
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time

BASE = "/root/astrbot-src/data/plugins/astrbot_plugin_higan"
MAIN = os.path.join(BASE, "main.py")
MEMPY = os.path.join(BASE, "memory.py")
GROUPS = "/root/higan/groups.json"

TOOLS = '''        if name in ("wake_list", "wake_add", "wake_remove", "wake_search"):
            # [wake-v2] 唤醒人/唤醒词：按群存、按群看、可删、可搜（不直接吐全量）
            try:
                _p = "/root/higan/groups.json"
                with open(_p, encoding="utf-8") as _h:
                    _c = json.load(_h)
                _wp = _c.get("wake_persons") or {}
                _ww = _c.get("wake_words") or {}
                if not isinstance(_wp, dict):
                    _wp = {}
                if not isinstance(_ww, dict):
                    _ww = {}
                _g = str(args.get("group") or "").strip() or str(group or "")
                if name == "wake_list":
                    _mode = str(args.get("mode") or "summary")
                    if _mode == "group" and _g:
                        return {"ok": True, "group": _g,
                                "persons": [str(x) for x in (_wp.get(_g) or [])],
                                "words": [str(x) for x in (_ww.get(_g) or [])]}
                    return {"ok": True, "mode": "summary",
                            "groups": {k: {"persons": len(v or []),
                                           "words": len(_ww.get(k) or [])}
                                       for k, v in _wp.items()},
                            "hint": "要看某个群的名单就传 group（例如本群）"}
                if name == "wake_search":
                    _q = str(args.get("query") or "").strip()
                    _hit_p, _hit_w = {}, {}
                    for _gid, _lst in _wp.items():
                        _f = [x for x in (_lst or []) if _q and _q in str(x)]
                        if _f:
                            _hit_p[_gid] = _f
                    for _gid, _lst in _ww.items():
                        _f = [x for x in (_lst or []) if _q and _q in str(x)]
                        if _f:
                            _hit_w[_gid] = _f
                    return {"ok": True, "query": _q,
                            "persons": _hit_p, "words": _hit_w}
                _qq = str(args.get("qq") or "").strip()
                _word = str(args.get("word") or "").strip()
                if name == "wake_add":
                    if _qq:
                        if not _qq.isdigit():
                            return {"ok": False,
                                    "error": "唤醒人必须用 QQ 号（纯数字），名字不可信"}
                        _lst = _wp.setdefault(_g, [])
                        if _qq not in _lst:
                            _lst.append(_qq)
                    elif _word:
                        _lst = _ww.setdefault(_g, [])
                        if _word not in _lst:
                            _lst.append(_word)
                    else:
                        return {"ok": False, "error": "要给 qq（唤醒人）或 word（唤醒词）"}
                else:  # wake_remove
                    if _qq:
                        _lst = _wp.get(_g) or []
                        if _qq in _lst:
                            _lst.remove(_qq)
                        _wp[_g] = _lst
                    if _word:
                        _lst = _ww.get(_g) or []
                        if _word in _lst:
                            _lst.remove(_word)
                        _ww[_g] = _lst
                _c["wake_persons"], _c["wake_words"] = _wp, _ww
                with open(_p, "w", encoding="utf-8") as _h:
                    json.dump(_c, _h, ensure_ascii=False, indent=2)
                return {"ok": True, "group": _g,
                        "persons": [str(x) for x in (_wp.get(_g) or [])],
                        "words": [str(x) for x in (_ww.get(_g) or [])]}
            except Exception as _exc:  # noqa: BLE001
                logger.warning("[higan] wake 工具失败：%r", str(_exc)[:120])
                return {"ok": False, "error": str(_exc)[:120]}

        if name in ("wp_fact_add", "wp_fact_list", "wp_fact_search", "wp_fact_delete"):
            # [wake-person-facts] 关于某个唤醒人的记录（单独存，最多 200 条，不计入 facts）
            try:
                import importlib.util as _ilu3
                from pathlib import Path as _P3
                _sp3 = _ilu3.spec_from_file_location(
                    "wp_mem", str(_P3(BASE_DIR, "memory.py")))
                _wpmod = _ilu3.module_from_spec(_sp3)
                _sp3.loader.exec_module(_wpmod)
                _st = _wpmod.WakePersonFacts("/root/higan/memory/wake_persons.json").load()
                _qq = str(args.get("qq") or "").strip()
                if name == "wp_fact_add":
                    _fid = _st.add(_qq, str(args.get("text") or ""))
                    return {"ok": bool(_fid), "id": _fid, "count": _st.count(_qq)}
                if name == "wp_fact_list":
                    return {"ok": True, "qq": _qq, "facts": _st.list(_qq)}
                if name == "wp_fact_search":
                    return {"ok": True, "qq": _qq,
                            "hits": _st.search(_qq, str(args.get("query") or ""))}
                _n = _st.delete(_qq, str(args.get("id") or ""),
                                str(args.get("keyword") or ""))
                return {"ok": True, "deleted": _n}
            except Exception as _exc:  # noqa: BLE001
                logger.warning("[higan] 唤醒人记录失败：%r", str(_exc)[:120])
                return {"ok": False, "error": str(_exc)[:120]}

'''

SCHEMAS = '''            _f("wake_list", "看唤醒人/唤醒词：不传 group 只给各群数量；传 group 才列出该群名单",
               {"mode": {"type": "string"}, "group": {"type": "string"}}, []),
            _f("wake_search", "按关键词查唤醒人/唤醒词（不要直接要全量列表）",
               {"query": {"type": "string"}}, ["query"]),
            _f("wake_add", "加唤醒人或唤醒词（唤醒人必须用 QQ 号）",
               {"qq": {"type": "string"}, "word": {"type": "string"},
                "group": {"type": "string"}}, []),
            _f("wake_remove", "删唤醒人或唤醒词", {"qq": {"type": "string"},
                                                  "word": {"type": "string"},
                                                  "group": {"type": "string"}}, []),
            _f("wp_fact_add", "记一条关于某个唤醒人的事（单独记，最多 200 条）",
               {"qq": {"type": "string"}, "text": {"type": "string"}}, ["qq", "text"]),
            _f("wp_fact_list", "看某个唤醒人的记录", {"qq": {"type": "string"}}, ["qq"]),
            _f("wp_fact_search", "在某个唤醒人的记录里按关键词找",
               {"qq": {"type": "string"}, "query": {"type": "string"}}, ["qq", "query"]),
            _f("wp_fact_delete", "删某个唤醒人的记录（按编号或关键词）",
               {"qq": {"type": "string"}, "id": {"type": "string"},
                "keyword": {"type": "string"}}, ["qq"]),
'''

WPFACTS = '''

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
'''


def compile_ok(path: str) -> bool:
    """语法检查。"""
    res = subprocess.run([sys.executable, "-m", "py_compile", path],
                         capture_output=True, text=True)
    if res.returncode != 0:
        print("  ✗ 编译失败", path, res.stderr[-250:])
    return res.returncode == 0


def main() -> int:
    """执行。"""
    changed = False

    src = open(MEMPY, encoding="utf-8").read()
    if "class WakePersonFacts" not in src:
        shutil.copy2(MEMPY, MEMPY + ".bak-wp-" + time.strftime("%Y%m%d-%H%M%S"))
        src = re.sub(r"^MAX_FACTS = \d+", "MAX_FACTS = 2000", src, flags=re.M)
        src = src.rstrip() + "\n" + WPFACTS
        with open(MEMPY, "w", encoding="utf-8") as handle:
            handle.write(src)
        if not compile_ok(MEMPY):
            return 1
        print("  ✓ memory.py：MAX_FACTS=2000 + WakePersonFacts(200/人)")
        changed = True

    msrc = open(MAIN, encoding="utf-8").read()
    mbak = MAIN + ".bak-wakev2-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(MAIN, mbak)
    before = msrc

    # 常量 BASE_DIR（给 wp_fact 用）
    if "BASE_DIR = str(" not in msrc:
        marker = "from __future__ import annotations\n"
        msrc = msrc.replace(
            marker,
            marker + "\nBASE_DIR = os.path.dirname(os.path.abspath(__file__))\n", 1)

    # schema：替换旧 wake_* 三行
    msrc = re.sub(
        r'            _f\("wake_list".*?\n            _f\("wake_remove".*?\n',
        SCHEMAS, msrc, flags=re.S, count=1)

    # 分发：整块替换旧的 wake_* 分支
    msrc = re.sub(
        r"        if name in \(\"wake_list\", \"wake_add\", \"wake_remove\"\):.*?"
        r"                return \{\"ok\": False, \"error\": str\(_exc\)\[:120\]\}\n",
        TOOLS, msrc, flags=re.S, count=1)

    # 唤醒人的消息加结构化标记（按 QQ 号，不看昵称）
    old_line = '''            line = "%s%s：%s%s" % (self._who(event), _tag, text or "[图片]",'''
    new_line = '''            _mark = ""
            try:
                _wp_all = json.load(open("/root/higan/groups.json",
                                         encoding="utf-8")).get("wake_persons") or {}
                _sender_qq = str(event.get_sender_id() or "")
                if _sender_qq and _sender_qq in [str(x) for x in
                                                 (_wp_all.get(str(group)) or [])]:
                    _mark = "【唤醒人 QQ %s】" % _sender_qq
            except Exception:  # noqa: BLE001
                _mark = ""
            line = "%s%s%s：%s%s" % (_mark, self._who(event), _tag, text or "[图片]",'''
    if old_line in msrc:
        msrc = msrc.replace(old_line, new_line, 1)

    if msrc != before:
        with open(MAIN, "w", encoding="utf-8") as handle:
            handle.write(msrc)
        if not compile_ok(MAIN):
            shutil.copy2(mbak, MAIN)
            return 1
        print("  ✓ main.py：唤醒工具重做 + 唤醒人标记（备份 %s）" % os.path.basename(mbak))
        changed = True

    # 迁移 groups.json：wake_persons/wake_words 改成按群 dict
    try:
        conf = json.load(open(GROUPS, encoding="utf-8"))
    except Exception:                                      # noqa: BLE001
        conf = {}
    for key in ("wake_persons", "wake_words"):
        val = conf.get(key)
        if not isinstance(val, dict):
            conf[key] = {}
    with open(GROUPS, "w", encoding="utf-8") as handle:
        json.dump(conf, handle, ensure_ascii=False, indent=2)
    print("  ✓ groups.json 唤醒表改为按群存储：", json.dumps(
        {k: conf.get(k) for k in ("wake_persons", "wake_words")}, ensure_ascii=False))

    if changed:
        subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
        time.sleep(22)
        st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                            capture_output=True, text=True).stdout.strip()
        print("  astrbot-186:", st or "unknown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
