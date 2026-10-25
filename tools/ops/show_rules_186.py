#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打印她的任务规则全文（DECISION_SYSTEM）+ 确认 wake_person_info 已注册（2026-10-25）。"""

from __future__ import annotations

import importlib.util
import re
import sys

PROMPTS = "/root/astrbot-src/data/plugins/astrbot_plugin_higan/prompts_v2.py"
MAIN = "/root/astrbot-src/data/plugins/astrbot_plugin_higan/main.py"


def main() -> int:
    """输出。"""
    spec = importlib.util.spec_from_file_location("pv2", PROMPTS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    text = getattr(mod, "DECISION_SYSTEM", "") or ""
    print("=== 任务规则 DECISION_SYSTEM（%d 字）===" % len(text))
    print(text)

    src = open(MAIN, encoding="utf-8").read()
    print("=== 核对 ===")
    print("  wake_person_info 出现:", len(re.findall(r"wake_person_info", src)), "次")
    print("  wake_person_set  出现:", len(re.findall(r"wake_person_set", src)), "次")
    print("  wake_at 相关:", len(re.findall(r"wake_at", src)), "次")
    return 0


if __name__ == "__main__":
    sys.exit(main())
