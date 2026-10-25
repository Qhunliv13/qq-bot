#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""去掉工具回话的额外限定语 + 新增"检索旧记忆"工具（2026-10-25）。

用户：
  · 不要加一堆限定，直接简单返回工具调用结果，不需要额外提示
  · 增加工具让她检索**已经遗忘的记忆**（磁盘上的历史档案）
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

MAIN = "/root/astrbot-src/data/plugins/astrbot_plugin_higan/main.py"

# ① 删掉那句额外 user 提示
OLD_EXTRA = '''            _msgs.append({"role": "user",
                          "content": "（上面是工具返回的资料，仅供参考。"
                                     "现在回她刚才那句话本身：她问什么你就答什么，"
                                     "别把资料当话题，也别报工具名和字段名。）"})
'''

# ② 新工具
SCHEMA_ANCHOR = '            _f("facts_list",'
SCHEMA_ADD = '''            _f("history_search", "在聊天档案里找我可能已经忘掉的内容（可按群/关键词）",
               {"query": {"type": "string"}, "group": {"type": "string"},
                "limit": {"type": "integer"}}, ["query"]),
            _f("history_recent", "看某个群最旧的若干条（从头翻档案）",
               {"group": {"type": "string"}, "count": {"type": "integer"}}, []),
'''

DISPATCH_ANCHOR = '        if name in ("facts_list", "facts_search", "facts_edit", "facts_delete"):'
DISPATCH_ADD = '''        if name in ("history_search", "history_recent"):
            # [history-recall] 检索磁盘上的完整聊天档案（她已遗忘的部分）
            try:
                import glob as _glob
                import json as _json
                _root = "/root/higan/history"
                _gid = str(args.get("group") or "").strip()
                _pat = (os.path.join(_root, "%s.jsonl" % _gid) if _gid
                        else os.path.join(_root, "*.jsonl"))
                _limit = int(args.get("limit") or 30)
                if name == "history_recent":
                    _limit = int(args.get("count") or 20)
                _q = str(args.get("query") or "").strip()
                _hits = []
                for _f in sorted(_glob.glob(_pat)):
                    try:
                        with open(_f, encoding="utf-8", errors="replace") as _h:
                            for _line in _h:
                                if _q and _q not in _line:
                                    continue
                                try:
                                    _rec = _json.loads(_line)
                                except Exception:  # noqa: BLE001
                                    continue
                                _hits.append({"file": os.path.basename(_f),
                                              "ts": _rec.get("ts"),
                                              "line": str(_rec.get("line") or "")[:300]})
                    except Exception:  # noqa: BLE001
                        continue
                if name == "history_search":
                    _hits = _hits[-_limit:]
                else:
                    _hits = _hits[:_limit]
                return {"ok": True, "count": len(_hits), "items": _hits}
            except Exception as _exc:  # noqa: BLE001
                logger.warning("[higan] 历史检索失败：%r", str(_exc)[:150])
                return {"ok": False, "error": str(_exc)[:150]}

'''


def main() -> int:
    """执行。"""
    src = open(MAIN, encoding="utf-8").read()
    changed = False
    bak = MAIN + ".bak-recall-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(MAIN, bak)

    if OLD_EXTRA in src:
        src = src.replace(OLD_EXTRA, "", 1)
        changed = True
        print("  ✓ 已去掉额外限定语（只回工具结果）")
    else:
        print("  - 额外限定语未找到（可能已改）")

    if '"history_search"' not in src:
        pos = src.index(SCHEMA_ANCHOR)
        start = src.rindex("\n", 0, pos) + 1
        src = src[:start] + SCHEMA_ADD + src[start:]
        pos = src.index(DISPATCH_ANCHOR)
        start = src.rindex("\n", 0, pos) + 1
        src = src[:start] + DISPATCH_ADD + src[start:]
        changed = True
        print("  ✓ 新增工具 history_search / history_recent")

    if not changed:
        return 0

    with open(MAIN, "w", encoding="utf-8") as handle:
        handle.write(src)
    res = subprocess.run([sys.executable, "-m", "py_compile", MAIN],
                         capture_output=True, text=True)
    if res.returncode != 0:
        shutil.copy2(bak, MAIN)
        print("  ✗ 编译失败已还原：", res.stderr[-300:])
        return 1
    print("  ✓ 编译通过（备份 %s）" % os.path.basename(bak))
    subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
    time.sleep(20)
    st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                        capture_output=True, text=True).stdout.strip()
    print("  astrbot-186:", st or "unknown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
