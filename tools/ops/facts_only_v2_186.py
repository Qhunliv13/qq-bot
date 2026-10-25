#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提示词尾部只留事实（按区块替换，不依赖精确文本）+ 拦截 DSML 泄漏（2026-10-25）。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time

BASE = "/root/astrbot-src/data/plugins/astrbot_plugin_higan"
MAIN = os.path.join(BASE, "main.py")
PROMPTS = os.path.join(BASE, "prompts_v2.py")

NEW_TAIL = '''    # [facts-only] 尾部只追加事实；状态/摘要/世界书/最近说过/现在判断全部去掉
    tail: list[str] = []
    facts = [str(f) for f in (facts or []) if str(f).strip()]
    if facts:
        tail.append("【你记得的事】" + "；".join(facts))

'''


def compile_ok(path: str) -> bool:
    """语法检查。"""
    res = subprocess.run([sys.executable, "-m", "py_compile", path],
                         capture_output=True, text=True)
    if res.returncode != 0:
        print("  ✗ 编译失败", path, res.stderr[-300:])
    return res.returncode == 0


def main() -> int:
    """执行。"""
    src = open(PROMPTS, encoding="utf-8").read()
    changed = False
    if "[facts-only]" not in src:
        bak = PROMPTS + ".bak-factsonly-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(PROMPTS, bak)
        start = src.find("    tail: list[str] = []")
        end = src.find("    msgs: list[dict] = [", start)
        if start < 0 or end < 0:
            print("  ✗ 找不到尾部区块起止")
            return 1
        src = src[:start] + NEW_TAIL + src[end:]
        with open(PROMPTS, "w", encoding="utf-8") as handle:
            handle.write(src)
        if not compile_ok(PROMPTS):
            return 1
        print("  ✓ 尾部只保留事实（备份 %s）" % os.path.basename(bak))
        changed = True

    msrc = open(MAIN, encoding="utf-8").read()
    if "_strip_tool_syntax" not in msrc:
        helper = '''
    @staticmethod
    def _strip_tool_syntax(text: str) -> str:
        """清掉模型误输出的工具调用语法，别发到群里。

        Args:
            text: 待发文本。

        Returns:
            str: 清理结果（可能为空，调用方按沉默处理）。
        """
        out = str(text or "")
        for pat in (r"<\\|+\\s*\\|?DSML[\\s\\S]*", r"<\\|[\\s\\S]*?\\|>",
                    r"<\\s*/?\\s*invoke[\\s\\S]*", r"<\\s*/?\\s*calls[\\s\\S]*"):
            out = re.sub(pat, "", out)
        return out.strip()

'''
        anchor = "    async def _call_llm(self, provider, prompt: str, extra_system: str,"
        if anchor not in msrc:
            print("  ✗ 找不到 _call_llm 锚点")
            return 1
        msrc = msrc.replace(anchor, helper + anchor, 1)
        mbak = MAIN + ".bak-leak-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(MAIN, mbak)
        with open(MAIN, "w", encoding="utf-8") as handle:
            handle.write(msrc)
        if not compile_ok(MAIN):
            return 1
        print("  ✓ 增加 _strip_tool_syntax（备份 %s）" % os.path.basename(mbak))
        changed = True

    if changed:
        subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
        time.sleep(20)
        st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                            capture_output=True, text=True).stdout.strip()
        print("  astrbot-186:", st or "unknown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
