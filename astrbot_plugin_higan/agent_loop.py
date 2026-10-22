#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工具回路（独立模块，可单测）—— 2026-10-20。

为什么独立：之前我把这段塞进 2000 行的 `_decide` 里，位置被前面的分支绕过 ✗，
而且没法脱离 AstrBot 单独验证 ✗。现在拆成纯函数：

    await run(system, prompt, llm, run_tool, max_rounds=3)

  llm(system, prompt) -> (text, tool_calls)     ← 注入：真实是直连 relay，测试时可替换
  run_tool(name, args) -> dict                  ← 注入：真实是 VM API
返回 (最终文本, 调用过的工具列表)

单测只需两个假函数，就能验证"会不会真的调工具、会不会把结果喂回去" ✓
"""

from __future__ import annotations

import json
from typing import Awaitable, Callable

MAX_ROUNDS = 3


async def run(system: str,
              prompt: str,
              llm: Callable[[str, str], Awaitable[tuple]],
              run_tool: Callable[[str, dict], Awaitable[dict]],
              max_rounds: int = MAX_ROUNDS) -> tuple:
    """跑最多 max_rounds 轮"调工具 → 看结果 → 继续"，返回 (文本, 用过的工具名列表)。

    关键点：
      · 每轮都把上一轮工具结果**追加到 prompt**（而不是覆盖）✓
      · 模型不给 tool_calls 就结束（不再硬循环）✓
      · 任何异常都收敛成 (当前文本, 已用工具) ✓ 上层可照常说话
    """
    used: list = []
    text = ""
    cur_prompt = prompt
    for _round in range(max(1, int(max_rounds))):
        text, calls = await llm(system, cur_prompt)
        if not calls:
            break
        for call in calls:
            fn = ((call.get("function") or {}).get("name") or "").strip()
            raw_args = ((call.get("function") or {}).get("arguments") or "{}")
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
            except Exception:  # noqa: BLE001
                args = {}
            if not fn:
                continue
            try:
                result = await run_tool(fn, args)
            except Exception as exc:  # noqa: BLE001
                result = {"ok": False, "error": repr(exc)[:200]}
            used.append(fn)
            cur_prompt = (prompt + "\n\n【%s 的执行结果】\n%s\n\n请据此继续：还需要工具就继续调用，"
                          "能回答了就正常说话。" % (fn, json.dumps(result, ensure_ascii=False)[:1200]))
        else:
            # 本轮有工具调用 ⇒ 再问一次（拿最终回答）
            continue
        break
    return (text or "").strip(), used
