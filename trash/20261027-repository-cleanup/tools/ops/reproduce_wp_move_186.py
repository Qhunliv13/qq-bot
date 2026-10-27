#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""直接复现 wp_fact_move 逻辑，定位"工具出岔子"（2026-10-25）。"""

from __future__ import annotations

import importlib.util
import os
import sys

BASE = "/root/astrbot-src/data/plugins/astrbot_plugin_higan"
MEM = "/root/higan/memory/memory.json"
WPF = "/root/higan/memory/wake_persons.json"


def main() -> int:
    """复现。"""
    spec = importlib.util.spec_from_file_location("hm", os.path.join(BASE, "memory.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    gm = mod.Memory(MEM).load()
    facts = gm.list_facts()
    print("  通用事实 %d 条：" % len(facts))
    for f in facts:
        print("   -", f["id"], str(f["text"])[:50])

    print("  有 WakePersonFacts 吗:", hasattr(mod, "WakePersonFacts"))
    st = mod.WakePersonFacts(WPF).load()
    print("  唤醒人记录文件可读:", st is not None)

    # 找一个疑似"瑞禾"的事实做移动测试
    target = None
    for f in facts:
        if "瑞禾" in str(f["text"]):
            target = f
            break
    if not target:
        print("  - 没有含「瑞禾」的事实，跳过移动测试")
        return 0
    print("  测试移动:", target["id"], "→ 3643349392")
    try:
        nid = st.add("3643349392", str(target["text"]))
        print("   写入唤醒人记录 ->", nid)
        gm.delete_fact(target["id"])
        print("   从通用删除 -> ok；现在 person count:", st.count("3643349392"))
    except Exception as exc:                               # noqa: BLE001
        import traceback
        traceback.print_exc()
    return 0


if __name__ == "__main__":
    sys.exit(main())
