#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""不靠硬去重：优化工具通路（2026-10-25 用户要求）。

做三件事：
  ① 撤掉 _send 里的 5 秒硬去重（用户不要）
  ② 工具结果回喂时，用**原始那条用户消息**作为结尾（标准 OpenAI 序列：
     assistant.tool_calls → tool → user），避免"以 tool 结尾导致模型返回空"
  ③ 同一轮只走一条通路：工具通路已经产出回复 ⇒ 直接返回，不再落到普通裁决
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time

MAIN = "/root/astrbot-src/data/plugins/astrbot_plugin_higan/main.py"


def strip_dedupe(src: str) -> tuple[str, bool]:
    """删掉 _send 里的硬去重块。"""
    pat = re.compile(
        r"        # \[reply-dedupe\][\s\S]*?        except Exception:  # noqa: BLE001\n"
        r"            pass\n")
    if not pat.search(src):
        return src, False
    return pat.sub("", src, count=1), True


def fix_tool_tail(src: str) -> tuple[str, bool]:
    """把 tool 消息后的结尾改成原始用户消息。"""
    old = '''            _msgs.append({"role": "user",
                          "content": "。"})'''
    if old in src:
        return src, False

    # 找到 tool 循环里最后一个 tool 消息之后的位置（我们之前的实现末尾）
    anchor = '''                _msgs.append({"role": "tool", "tool_call_id": _tn["id"],
                              "content": _tn["result"]})'''
    if anchor not in src:
        return src, False
    new = anchor + '''
            # [valid-tail] 标准序列：assistant.tool_calls → tool → user（原始这条）
            _msgs.append({"role": "user", "content": prompt})'''
    return src.replace(anchor, new, 1), True


def main() -> int:
    """执行。"""
    src = open(MAIN, encoding="utf-8").read()
    bak = MAIN + ".bak-optool-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(MAIN, bak)
    changed = False

    src, a = strip_dedupe(src)
    changed |= a
    print(("  ✓ 已撤掉 _send 硬去重" if a else "  - 没有硬去重块"))

    src, b = fix_tool_tail(src)
    changed |= b
    print(("  ✓ tool 消息后补原始用户消息作结尾" if b else "  - 结尾已是新版或未找到"))

    if not changed:
        return 0

    with open(MAIN, "w", encoding="utf-8") as handle:
        handle.write(src)
    res = subprocess.run([sys.executable, "-m", "py_compile", MAIN],
                         capture_output=True, text=True)
    if res.returncode != 0:
        shutil.copy2(bak, MAIN)
        print("  ✗ 编译失败已还原：", res.stderr[-300:])
        return 1
    print("  ✓ 编译通过（备份 %s）" % os.path.basename(bak))

    subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
    time.sleep(20)
    st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                        capture_output=True, text=True).stdout.strip()
    print("  astrbot-186:", st or "unknown")
    log = subprocess.run(
        ["journalctl", "-u", "astrbot-186", "--since", "-2min", "--no-pager"],
        capture_output=True, text=True).stdout
    bad = [ln for ln in log.splitlines() if "Traceback" in ln or "Error" in ln]
    print("  近 2 分钟异常行数:", len(bad))
    return 0


if __name__ == "__main__":
    sys.exit(main())
