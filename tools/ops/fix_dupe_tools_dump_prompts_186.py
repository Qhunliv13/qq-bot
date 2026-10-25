#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修 400：工具名重复 + 打印她的系统提示词与上下文示例（2026-10-25）。

上游原文：LITELLM_ERROR: Tool names must be unique
原因：我多次追加工具 ⇒ schema 列表出现同名重复（旧 wake_* 与 新 wake_* 并存等）
修法：在 _tool_schemas 的 return 外面套一层按名去重（保留最后一个定义）
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time

BASE = "/root/astrbot-src/data/plugins/astrbot_plugin_higan"
MAIN = os.path.join(BASE, "main.py")
PROMPTS = os.path.join(BASE, "prompts_v2.py")
DB = "/root/astrbot-src/data/data_v4.db"

HELPER = '''        def _dedupe_schemas(items: list) -> list:
            """按 function.name 去重（保留最后一次定义，避免上游 400）。"""
            seen: dict = {}
            order: list = []
            for it in items:
                name = ((it.get("function") or {}).get("name") or "")
                if not name:
                    continue
                if name not in seen:
                    order.append(name)
                seen[name] = it
            return [seen[n] for n in order]

'''


def patch_schemas() -> bool:
    """给 _tool_schemas 加去重。"""
    src = open(MAIN, encoding="utf-8").read()
    if "_dedupe_schemas" in src:
        print("  - 去重已存在")
        return False

    anchor = "        def _f(name, desc, props=None, required=None):"
    if anchor not in src:
        print("  ✗ 找不到 _f 定义")
        return False
    src = src.replace(anchor, HELPER + anchor, 1)

    # 找到 _tool_schemas 里 return [ ... ] 的块
    start = src.index("        def _tool_schemas(self) -> list:")
    seg = src[start:start + 20000]
    mt = re.match(r"(?s)(.*?)(        return \[\n)(.*?)(\n        \]\n)", seg)
    if not mt:
        print("  ✗ 找不到 return [ 块")
        return False
    new_seg = (mt.group(1) + "        return _dedupe_schemas([\n"
               + mt.group(3) + "\n        ])\n" + seg[mt.end():])
    src = src[:start] + new_seg + src[start + len(seg):]

    bak = MAIN + ".bak-dedupe-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(MAIN, bak)
    with open(MAIN, "w", encoding="utf-8") as handle:
        handle.write(src)
    res = subprocess.run([sys.executable, "-m", "py_compile", MAIN],
                         capture_output=True, text=True)
    if res.returncode != 0:
        shutil.copy2(bak, MAIN)
        print("  ✗ 编译失败已还原：", res.stderr[-400:])
        return False
    print("  ✓ 工具 schema 去重（备份 %s）" % os.path.basename(bak))
    return True


def dump_prompts() -> None:
    """打印系统提示词与消息示例。"""
    con = sqlite3.connect(DB)
    row = con.execute("select system_prompt from personas "
                      "where persona_id='higan'").fetchone()
    con.close()
    persona = (row[0] if row else "") or ""
    print("\n=== ① 人格（库里 higan，%d 字）===" % len(persona))
    print(persona)

    spec = importlib.util.spec_from_file_location("pv2", PROMPTS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    decision = getattr(mod, "DECISION_SYSTEM", "")
    print("\n=== ② 决策规则 DECISION_SYSTEM（%d 字）===" % len(decision))
    print(decision)

    print("\n=== ③ 消息结构示例 ===")
    msgs = mod.build_messages(
        recent_lines=["主人（主人，QQ <OWNER_QQ>）#1：在吗",
                      "你（彼岸酱）：在的"],
        latest="主人（主人，QQ <OWNER_QQ>）#2：你现在回复率多少",
        signals={"at_me": True, "at_others": [], "called_name": False},
        summary="群里在聊 API 价格",
        facts=["摸头怪是唤醒人", "瑞禾福佑送过蛋糕"])
    for i, m in enumerate(msgs):
        body = m["content"] if isinstance(m["content"], str) else json.dumps(m["content"])
        print("  [%d] role=%s len=%d" % (i, m["role"], len(body)))
        print("      " + body[:500].replace("\n", "\n      "))


def main() -> int:
    """执行。"""
    changed = patch_schemas()
    dump_prompts()
    if changed:
        subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
        time.sleep(20)
        st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                            capture_output=True, text=True).stdout.strip()
        print("\n  astrbot-186:", st or "unknown")
    return 0


if __name__ == "__main__":
    import importlib.util

    sys.exit(main())
