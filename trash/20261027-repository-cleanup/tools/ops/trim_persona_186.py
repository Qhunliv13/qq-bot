#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按用户批示精简人格，并把 Q-API 资料搬进事实（2026-10-25）。

删：
  · 「# 人称（别搞混）」整段
  · 「# 人设是核心（最重要）」整段
  · 「：礼貌、可玩闹，但不叫主人、不撒娇。」这个从句
  · 「好例子 / 坏例子」两段示例
  · 「对所有人用「你」，不用「您」。」
搬：
  · 「# 你了解的信息（Q-API，简单版）」整段 → 写进 memory.json 的 facts
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time

BASE = "/root/astrbot-src/data/plugins/astrbot_plugin_higan"
DB = "/root/astrbot-src/data/data_v4.db"
MEM = "/root/higan/memory/memory.json"


def load_memory_mod():
    """加载她的 memory 模块。"""
    spec = importlib.util.spec_from_file_location("hm", os.path.join(BASE, "memory.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    """执行。"""
    con = sqlite3.connect(DB)
    row = con.execute("select system_prompt from personas where persona_id='higan'").fetchone()
    if not row:
        print("  ✗ 没有 higan 人设")
        return 1
    text = row[0] or ""
    before = len(text)

    # ① 人称段
    text = re.sub(r"# 人称（别搞混）[\s\S]*?(?=\n# )", "", text)
    # ② 人设是核心段
    text = re.sub(r"# 人设是核心（最重要）[\s\S]*?(?=\n# )", "", text)
    # ③ 群友从句
    text = text.replace("：礼貌、可玩闹，但不叫主人、不撒娇。", "。")
    # ④ 好例子/坏例子
    text = re.sub(r"\*\*好例子（学这些）\*\*：[\s\S]*?(?=\*\*坏例子)", "", text)
    text = re.sub(r"\*\*坏例子（绝对不要）\*\*：[\s\S]*?(?=\n对所有人)", "", text)
    # ⑤ 人称指代句
    text = text.replace("对所有人用「你」，不用「您」。\n", "")
    # ⑥ 摘出 Q-API 段，搬去事实
    qapi = ""
    mt = re.search(r"# 你了解的信息（Q-API[^\n]*\n([\s\S]*?)$", text)
    if mt:
        qapi = mt.group(1).strip()
        text = text[:mt.start()].rstrip() + "\n"

    text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
    shutil.copy2(DB, DB + ".bak-trim-" + time.strftime("%Y%m%d-%H%M%S"))
    con.execute("update personas set system_prompt=? where persona_id='higan'", (text,))
    con.commit()
    con.close()
    print("  ✓ 人格精简：%d → %d 字" % (before, len(text)))

    if qapi:
        mod = load_memory_mod()
        mem = mod.Memory(MEM).load()
        added = 0
        for line in qapi.splitlines():
            item = line.strip(" ·-")
            if len(item) < 6:
                continue
            fid = mem.add_fact("Q-API：" + item, "persona-moved")
            added += 1 if fid else 0
        print("  ✓ Q-API 资料搬进事实：%d 条（当前 facts 共 %d 条）"
              % (added, len(mem.list_facts())))

    return 0


if __name__ == "__main__":
    sys.exit(main())
