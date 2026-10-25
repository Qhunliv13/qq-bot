#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修"上句不接下句"的真因：工具通路把整段 prompt 重复塞了两次（2026-10-25）。

现状（代码结构）：
  prompt = "【聊天记录】…【群X（现在）】…\n\n现在要对你说的话：<text>"
  messages = [system, user(prompt), assistant.tool_calls, tool(结果)…, user(prompt)]  ← 同一段出现两次
⇒ 模型在"已经回复过同一段"的状态下再看到它 + 中间夹着工具结果 ⇒ 接不上 ✗

修法：末尾那条 user 只放**当前这一句**（带发言人标注），不再重复整段 prompt。
同时修 history_search 的描述（上次没匹配到）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

MAIN = "/root/astrbot-src/data/plugins/astrbot_plugin_higan/main.py"

OLD_TAIL = '''            # [valid-tail] 标准序列：assistant.tool_calls → tool → user（原始这条）
            _msgs.append({"role": "user", "content": prompt})'''

NEW_TAIL = '''            # [valid-tail] 结尾只放**当前这一句**（不要重复整段提示，否则她会接不上）
            _cur_line = "%s：%s" % (self._who(event), text or "[图片]")
            _msgs.append({"role": "user", "content": _cur_line})'''

DESC_OLD = '_f("history_search", "翻自己的聊天记录：按关键词找以前说过的话",'
DESC_NEW = ('_f("history_search", "翻**聊天档案**（完整历史，含我已遗忘的部分）：'
            '某人以前说过什么、之前发生过的对话都从这里查；可按群限定、按关键词过滤",')


def main() -> int:
    """执行。"""
    src = open(MAIN, encoding="utf-8").read()
    changed = False
    bak = MAIN + ".bak-tail-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(MAIN, bak)

    if OLD_TAIL in src:
        src = src.replace(OLD_TAIL, NEW_TAIL, 1)
        changed = True
        print("  ✓ 工具通路结尾改为“当前这一句”（不再重复整段）")
    else:
        print("  - 结尾已是新版或未匹配")

    if DESC_OLD in src:
        src = src.replace(DESC_OLD, DESC_NEW, 1)
        changed = True
        print("  ✓ history_search 描述已更新")
    else:
        print("  - history_search 描述未匹配")

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
