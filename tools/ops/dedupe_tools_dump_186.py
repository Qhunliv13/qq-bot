#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工具名去重（模块级函数版）+ 打印提示词（2026-10-25）。

上游 400 原文：LITELLM_ERROR: Tool names must be unique
做法：加一个模块级 _dedupe_tool_schemas()，把所有 self._tool_schemas() 调用包一层。
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time

BASE = "/root/astrbot-src/data/plugins/astrbot_plugin_higan"
MAIN = os.path.join(BASE, "main.py")
PROMPTS = os.path.join(BASE, "prompts_v2.py")
DB = "/root/astrbot-src/data/data_v4.db"

MOD_FN = '''

def _dedupe_tool_schemas(items: list) -> list:
    """按 function.name 去重（保留最后一次定义；上游不接受重名）。"""
    seen: dict = {}
    order: list = []
    for it in items or []:
        try:
            name = ((it.get("function") or {}).get("name") or "")
        except Exception:                                  # noqa: BLE001
            continue
        if not name:
            continue
        if name not in seen:
            order.append(name)
        seen[name] = it
    return [seen[n] for n in order]

'''


def patch() -> bool:
    """打补丁。"""
    src = open(MAIN, encoding="utf-8").read()
    if "_dedupe_tool_schemas" in src:
        print("  - 去重已存在")
        return False
    marker = "from __future__ import annotations\n"
    src = src.replace(marker, marker + MOD_FN, 1)
    n = src.count("self._tool_schemas()")
    src = src.replace("self._tool_schemas()",
                      "_dedupe_tool_schemas(self._tool_schemas())")
    bak = MAIN + ".bak-dedupe2-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(MAIN, bak)
    with open(MAIN, "w", encoding="utf-8") as handle:
        handle.write(src)
    res = subprocess.run([sys.executable, "-m", "py_compile", MAIN],
                         capture_output=True, text=True)
    if res.returncode != 0:
        shutil.copy2(bak, MAIN)
        print("  ✗ 编译失败已还原：", res.stderr[-400:])
        return False
    print("  ✓ 去重接入 %d 处调用（备份 %s）" % (n, os.path.basename(bak)))
    return True


def dump() -> None:
    """打印提示词与示例。"""
    con = sqlite3.connect(DB)
    row = con.execute("select system_prompt from personas where persona_id='higan'").fetchone()
    con.close()
    persona = (row[0] if row else "") or ""
    print("\n=== ① 人格（%d 字）===" % len(persona))
    print(persona)

    spec = importlib.util.spec_from_file_location("pv2", PROMPTS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    decision = getattr(mod, "DECISION_SYSTEM", "")
    print("\n=== ② DECISION_SYSTEM（%d 字）===" % len(decision))
    print(decision)

    print("\n=== ③ 消息示例 ===")
    msgs = mod.build_messages(
        recent_lines=["主人（主人，QQ <OWNER_QQ>）#1：在吗"],
        latest="主人（主人，QQ <OWNER_QQ>）#2：现在你的回复率是多少",
        signals={"at_me": True, "at_others": [], "called_name": False},
        summary="群里在聊 API 价格",
        facts=["摸头怪是唤醒人"])
    for i, m in enumerate(msgs):
        body = m["content"]
        print("  [%d] role=%s len=%d" % (i, m["role"], len(body)))
        print("      " + body[:600].replace("\n", "\n      "))


def main() -> int:
    """执行。"""
    changed = patch()
    dump()
    if changed:
        subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
        time.sleep(20)
        st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                            capture_output=True, text=True).stdout.strip()
        print("\n  astrbot-186:", st or "unknown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
