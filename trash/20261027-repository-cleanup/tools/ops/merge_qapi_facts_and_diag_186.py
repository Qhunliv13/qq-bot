#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 Q-API 资料合并成一条事实 + 诊断"又不回复"（2026-10-25）。"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys

BASE = "/root/astrbot-src/data/plugins/astrbot_plugin_higan"
MEM = "/root/higan/memory/memory.json"


def main() -> int:
    """执行。"""
    spec = importlib.util.spec_from_file_location("hm", os.path.join(BASE, "memory.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    mem = mod.Memory(MEM).load()
    facts = mem.list_facts()
    qapi = [f for f in facts if str(f.get("text") or "").startswith("Q-API：")]
    print("  现有事实 %d 条，其中 Q-API 相关 %d 条" % (len(facts), len(qapi)))

    if len(qapi) > 1:
        lines = [str(f["text"]).replace("Q-API：", "", 1).strip() for f in qapi]
        merged = "Q-API（主人的 AI API 中转站）：" + "；".join(
            line.rstrip("；。") for line in lines if line)
        removed = mem.delete_fact(keyword="Q-API：")
        fid = mem.add_fact(merged, "consolidated")
        print("  ✓ 删除 %d 条散条 → 合并成 1 条（%s，%d 字）"
              % (removed, fid, len(merged)))
        print("  合并后事实总数:", len(mem.list_facts()))
    else:
        print("  - 已经是合并状态或只有 0/1 条")

    print("\n=== 诊断：最近日志 ===")
    out = subprocess.run(
        ["journalctl", "-u", "astrbot-186", "--since", "-10min", "--no-pager"],
        capture_output=True, text=True)
    keys = ("收到群", "产出回复", "沉默", "Error", "Traceback", "处理消息出错", "400")
    lines = [ln for ln in out.stdout.splitlines() if any(k in ln for k in keys)]
    for ln in lines[-18:]:
        print("  " + ln[-170:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
