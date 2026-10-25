#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""终极去重：在发请求前对 payload["tools"] 按名去重（2026-10-25）。

上游 400 仍然报 Tool names must be unique ⇒ 说明重名不在 _tool_schemas 内部，
而是在**最终 payload**（可能与 AstrBot 自带工具管理器并集）。
这里在 _llm_tools 与 _llm_chat 组装 payload 之后、发请求之前，统一去重。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time

MAIN = "/root/astrbot-src/data/plugins/astrbot_plugin_higan/main.py"

PAIRS = [
    # _llm_tools
    ('''            "tools": _dedupe_tool_schemas(self._tool_schemas()),
            "tool_choice": "auto",
        }''',
     '''            "tools": _dedupe_tool_schemas(self._tool_schemas()),
            "tool_choice": "auto",
        }
        payload["tools"] = _dedupe_tool_schemas(payload.get("tools") or [])'''),
    # _llm_chat
    ('''        if tools:
            payload["tools"] = _dedupe_tool_schemas(self._tool_schemas())
            payload["tool_choice"] = "auto"''',
     '''        if tools:
            payload["tools"] = _dedupe_tool_schemas(self._tool_schemas())
            payload["tool_choice"] = "auto"
        payload["tools"] = _dedupe_tool_schemas(payload.get("tools") or [])'''),
]


def main() -> int:
    """执行。"""
    src = open(MAIN, encoding="utf-8").read()
    changed = False
    bak = MAIN + ".bak-dedupe3-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(MAIN, bak)
    for old, new in PAIRS:
        if old in src and new not in src:
            src = src.replace(old, new, 1)
            changed = True
    if not changed:
        print("  - 无需改动或锚点不匹配")
        return 0
    with open(MAIN, "w", encoding="utf-8") as handle:
        handle.write(src)
    res = subprocess.run([sys.executable, "-m", "py_compile", MAIN],
                         capture_output=True, text=True)
    if res.returncode != 0:
        shutil.copy2(bak, MAIN)
        print("  ✗ 编译失败已还原：", res.stderr[-300:])
        return 1
    print("  ✓ payload 级去重已加（备份 %s）" % os.path.basename(bak))
    subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
    time.sleep(20)
    st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                        capture_output=True, text=True).stdout.strip()
    print("  astrbot-186:", st or "unknown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
