#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打印她现在的上下文格式（真实函数产出，2026-10-25）。"""

from __future__ import annotations

import importlib.util
import json
import sys

PROMPTS = "/root/astrbot-src/data/plugins/astrbot_plugin_higan/prompts_v2.py"


def main() -> int:
    """输出示例。"""
    spec = importlib.util.spec_from_file_location("pv2", PROMPTS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    recent = [
        "qhunliv 收1991年前的电脑设备硬件等，收ddr45CPU（主人，QQ <OWNER_QQ>）#1：在吗",
        "你（彼岸酱）：在的喵",
        "⁥⁬⁢（群友，QQ 544149173）#2：（这是 ⁥⁬⁢（群友，QQ 544149173） 发的图片/文件）",
    ]
    latest = "⁥⁬⁢（群友，QQ 544149173）#3：这图可以"
    msgs = mod.build_messages(
        recent_lines=recent,
        latest=latest,
        signals={"at_me": False, "at_others": [], "called_name": False},
        facts=["瑞禾＝QQ 3643349392，别喊全名", "Q-API（主人的中转站）：接口 <YOUR_DOMAIN>/v1"])

    print("=== ① 普通聊天通路（当前真实产出）===")
    for i, m in enumerate(msgs):
        print("[%d] role=%s len=%d" % (i, m["role"], len(m["content"])))
        print("    " + m["content"].replace("\n", "\n    "))
        print()

    print("=== ② 工具通路（工具结果以标准 tool 消息回喂）===")
    seq = [
        {"role": "system", "content": "（她的人格全文，1290 字）"},
        {"role": "user", "content": "（上面①里那整段：聊天记录 + 现在要对你说的话）"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "call_1_history_search", "type": "function",
                         "function": {"name": "history_search",
                                      "arguments": "{\"query\":\"酒狐\"}"}}]},
        {"role": "tool", "tool_call_id": "call_1_history_search",
         "content": "{\"ok\":true,\"count\":30,\"items\":[…30 条…]}"},
        {"role": "user", "content": "⁥⁬⁢（群友，QQ 544149173）：这图可以"},
    ]
    print(json.dumps(seq, ensure_ascii=False, indent=2)[:1200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
