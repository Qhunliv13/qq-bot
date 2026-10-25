#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""唤醒 v2（第二版实现）：先回滚坏掉的 main.py，再用"追加"方式接上（2026-10-25）。

上一版用正则整块替换 schema，括号没配平导致编译失败 ✗
这版做法：
  1. 从最近的 .bak-wakev2-* 还原 main.py
  2. 把旧 wake_* 分发分支停用（条件改 False），避免与新工具冲突
  3. 追加新 schema（8 个工具）到 facts_list 之前
  4. 追加新分发块到 facts_* 分支之前
  5. 消息行的唤醒人标记单独补
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
import time

BASE = "/root/astrbot-src/data/plugins/astrbot_plugin_higan"
MAIN = os.path.join(BASE, "main.py")
GROUPS = "/root/higan/groups.json"

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


def restore() -> bool:
    """从最近的备份还原 main.py。"""
    cands = sorted(glob.glob(MAIN + ".bak-wakev2-*"), reverse=True)
    if not cands:
        print("  ✗ 没有可还原的备份")
        return False
    shutil.copy2(cands[0], MAIN)
    print("  ✓ 已还原 main.py（备份 %s）" % os.path.basename(cands[0]))
    return True


def main() -> int:
    """执行。"""
    if not restore():
        return 1

    src = open(MAIN, encoding="utf-8").read()

    # 停用旧 wake 分支
    src = src.replace(
        'if name in ("wake_list", "wake_add", "wake_remove"):',
        'if name in ("__wake_v1_disabled__",):', 1)

    # 追加新 schema
    anchor = '            _f("facts_list",'
    pos = src.index(anchor)
    start = src.rindex("\n", 0, pos) + 1
    src = src[:start] + SCHEMAS + src[start:]

    # 追加新分发（放在 facts_* 分支之前）
    danchor = '        if name in ("facts_list", "facts_search", "facts_edit", "facts_delete"):'
    dpos = src.index(danchor)
    dstart = src.rindex("\n", 0, dpos) + 1
    tools = open("/root/wake_tools_block.py", encoding="utf-8").read()
    src = src[:dstart] + tools + src[dstart:]

    with open(MAIN, "w", encoding="utf-8") as handle:
        handle.write(src)

    res = subprocess.run([sys.executable, "-m", "py_compile", MAIN],
                         capture_output=True, text=True)
    if res.returncode != 0:
        print("  ✗ 编译失败：", res.stderr[-400:])
        return 1
    print("  ✓ 新 schema + 分发已接入（%d 字节）" % os.path.getsize(MAIN))

    conf = json.load(open(GROUPS, encoding="utf-8"))
    for key in ("wake_persons", "wake_words"):
        if not isinstance(conf.get(key), dict):
            conf[key] = {}
    json.dump(conf, open(GROUPS, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("  ✓ groups.json 唤醒表按群存储 ✓")

    subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
    time.sleep(22)
    st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                        capture_output=True, text=True).stdout.strip()
    print("  astrbot-186:", st or "unknown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
