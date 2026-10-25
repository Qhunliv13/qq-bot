#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""优化检索类工具的**描述**（不动任务提示词）（2026-10-25 用户选 B）。

要点：
  · facts_* 只查"已经记下来的事实"
  · history_search 查"聊天档案"（她遗忘的部分）——描述里写清什么时候该用
这样模型自己会选对工具，不用在人格/规则里加指令。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time

MAIN = "/root/astrbot-src/data/plugins/astrbot_plugin_higan/main.py"

REPL = [
    (
        '_f("facts_search", "按关键词找我记得的事", {"query": {"type": "string"},\n'
        '                                                       "limit": {"type": "integer"}}, ["query"]),',
        '_f("facts_search", "按关键词查**我已经记下来的事实**（长期记忆条目）；'
        '查不到不代表没发生过，那说明要去翻聊天档案 history_search",'
        ' {"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),',
    ),
    (
        '_f("history_search", "在聊天档案里找我可能已经忘掉的内容（可按群/关键词）",',
        '_f("history_search", "翻**聊天档案**（完整历史记录，含我已遗忘的部分）：'
        '记忆里没有的旧事、某人以前说过什么、之前发生过的对话，都从这里查；'
        '可按群限定、按关键词过滤",',
    ),
    (
        '_f("history_recent", "看某个群最旧的若干条（从头翻档案）",',
        '_f("history_recent", "从聊天档案最开头翻（想看这个群最早都聊了什么时用）",',
    ),
]


def main() -> int:
    """执行。"""
    src = open(MAIN, encoding="utf-8").read()
    changed = False
    for old, new in REPL:
        if old in src:
            src = src.replace(old, new, 1)
            changed = True
    if not changed:
        print("  - 已是新版描述或无匹配")
        return 0
    bak = MAIN + ".bak-tool-desc-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(MAIN, bak)
    with open(MAIN, "w", encoding="utf-8") as handle:
        handle.write(src)
    res = subprocess.run([sys.executable, "-m", "py_compile", MAIN],
                         capture_output=True, text=True)
    if res.returncode != 0:
        shutil.copy2(bak, MAIN)
        print("  ✗ 编译失败已还原：", res.stderr[-300:])
        return 1
    print("  ✓ 工具描述已改（备份 %s）" % os.path.basename(bak))

    # 打印改后的三行，便于核对
    text = open(MAIN, encoding="utf-8").read()
    for name in ("facts_search", "history_search", "history_recent"):
        mt = re.search(r'_f\("%s", "([^"]{0,120})' % name, text)
        print("  · %-16s %s" % (name, mt.group(1)[:100] if mt else "(未找到)"))

    subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
    time.sleep(20)
    st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                        capture_output=True, text=True).stdout.strip()
    print("  astrbot-186:", st or "unknown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
