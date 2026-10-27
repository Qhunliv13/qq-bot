#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出她的三样东西：人格全文 / 任务规则全文 / 工具定义全文（2026-10-25）。

说明：
  · "任务规则" = 代码里作为 extra_system 传给模型的那段（prompts_v2.DECISION_SYSTEM）
  · 人格 = AstrBot 库 personas(higan).system_prompt
  · 工具定义 = main.py 里 _tool_schemas() 的完整 JSON（在沙箱里执行该函数取得）
输出：/root/export/{persona.txt,decision_system.txt,tools.json}
"""

from __future__ import annotations

import ast
import json
import os
import sqlite3
import sys
import textwrap

BASE = "/root/astrbot-src/data/plugins/astrbot_plugin_higan"
MAIN = os.path.join(BASE, "main.py")
PROMPTS = os.path.join(BASE, "prompts_v2.py")
DB = "/root/astrbot-src/data/data_v4.db"
OUT = "/root/export"


def dump_persona() -> str:
    """写人格全文。"""
    con = sqlite3.connect(DB)
    row = con.execute("select system_prompt from personas where persona_id='higan'").fetchone()
    con.close()
    text = (row[0] if row else "") or ""
    with open(os.path.join(OUT, "persona.txt"), "w", encoding="utf-8") as handle:
        handle.write(text)
    return text


def dump_decision() -> str:
    """写任务规则全文。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("pv2", PROMPTS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    text = getattr(mod, "DECISION_SYSTEM", "") or ""
    with open(os.path.join(OUT, "decision_system.txt"), "w", encoding="utf-8") as handle:
        handle.write(text)
    return text


def dump_tools() -> list:
    """在沙箱里执行 _tool_schemas() 取完整 schema。"""
    src = open(MAIN, encoding="utf-8").read()
    tree = ast.parse(src)
    body = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_tool_schemas":
            body = ast.get_source_segment(src, node)
            break
    if not body:
        return []
    code = textwrap.dedent(body)
    ns: dict = {}

    class _Dummy:
        """占位 self：任务类工具返回空表。"""

        def _task_tool_schemas(self):
            """返回空工具表。"""
            return []

    def _dedupe(items):
        """按名去重。"""
        seen = {}
        order = []
        for it in items or []:
            name = ((it.get("function") or {}).get("name") or "")
            if not name:
                continue
            if name not in seen:
                order.append(name)
            seen[name] = it
        return [seen[n] for n in order]

    ns["_dedupe_tool_schemas"] = _dedupe
    exec(compile(code, "<schemas>", "exec"), ns)          # noqa: S102
    fn = ns.get("_tool_schemas")
    if not fn:
        return []
    raw = fn(_Dummy())
    tools = _dedupe(raw)
    with open(os.path.join(OUT, "tools.json"), "w", encoding="utf-8") as handle:
        json.dump(tools, handle, ensure_ascii=False, indent=2)
    return tools


def main() -> int:
    """执行。"""
    os.makedirs(OUT, exist_ok=True)
    persona = dump_persona()
    decision = dump_decision()
    tools = dump_tools()
    print("  人格: %d 字 → %s/persona.txt" % (len(persona), OUT))
    print("  任务规则: %d 字 → %s/decision_system.txt" % (len(decision), OUT))
    print("  工具: %d 个 → %s/tools.json" % (len(tools), OUT))
    for t in tools:
        fn = (t.get("function") or {})
        print("   · %-18s %s" % (fn.get("name"), (fn.get("description") or "")[:48]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
