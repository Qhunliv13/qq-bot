#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""历史不再按条数限制：只在接近 100 万 token 时丢掉最早 60%（2026-10-25 用户要求）。

改动：
  ① FORGET_KEEP_LINES 改成 FORGET_TOKEN_LIMIT = 1_000_000（按字符近似 token）
     触发条件：history 累计字符数 > 1,000,000 ⇒ 丢弃最早 60%
  ② prompts_v2.build_messages 的 max_history 默认从 10000 提到 200000（等于不限制）
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time

MAIN = "/root/astrbot-src/data/plugins/astrbot_plugin_higan/main.py"
PROMPTS = "/root/astrbot-src/data/plugins/astrbot_plugin_higan/prompts_v2.py"

OLD_CONST = '''# [forget-not-compress] 上下文只留这么多条；超了就忘掉最早的 60%（不压缩、不摘要）
FORGET_KEEP_LINES = 400
FORGET_RATIO = 0.6'''

NEW_CONST = '''# [forget-not-compress] 不按条数限制；只在接近 100 万 token 时忘掉最早 60%
# 中文近似：1 字符 ≈ 1 token，故用"累计字符数 > 1,000,000"作为触发条件
FORGET_TOKEN_LIMIT = 1000000
FORGET_RATIO = 0.6'''

OLD_BLOCK = '''            try:
                if len(history) > FORGET_KEEP_LINES:
                    _drop = int(len(history) * FORGET_RATIO)
                    logger.info("[higan] 群 %s：忘记最早的 %d 条对话（%.0f%%，剩 %d 条）"
                                % (group, _drop, FORGET_RATIO * 100, len(history) - _drop))
                    del history[:_drop]
            except Exception as _fexc:  # noqa: BLE001
                logger.warning("[higan] 忘记旧对话失败（忽略）：%r", str(_fexc)[:100])'''

NEW_BLOCK = '''            try:
                _chars = sum(len(str(x)) for x in history)
                if _chars > FORGET_TOKEN_LIMIT:
                    _drop = int(len(history) * FORGET_RATIO)
                    logger.info("[higan] 群 %s：上下文约 %d 字符（>%d）⇒ 忘记最早 %d 条（%.0f%%）"
                                % (group, _chars, FORGET_TOKEN_LIMIT, _drop,
                                   FORGET_RATIO * 100))
                    del history[:_drop]
            except Exception as _fexc:  # noqa: BLE001
                logger.warning("[higan] 忘记旧对话失败（忽略）：%r", str(_fexc)[:100])'''


def main() -> int:
    """执行。"""
    changed = False
    src = open(MAIN, encoding="utf-8").read()
    if "FORGET_TOKEN_LIMIT" not in src:
        bak = MAIN + ".bak-tokenlimit-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(MAIN, bak)
        src = src.replace(OLD_CONST, NEW_CONST, 1)
        if OLD_BLOCK in src:
            src = src.replace(OLD_BLOCK, NEW_BLOCK, 1)
        with open(MAIN, "w", encoding="utf-8") as handle:
            handle.write(src)
        res = subprocess.run([sys.executable, "-m", "py_compile", MAIN],
                             capture_output=True, text=True)
        if res.returncode != 0:
            shutil.copy2(bak, MAIN)
            print("  ✗ 编译失败已还原：", res.stderr[-300:])
            return 1
        print("  ✓ 历史改为按 token 上限（100 万）触发（备份 %s）" % os.path.basename(bak))
        changed = True

    psrc = open(PROMPTS, encoding="utf-8").read()
    if "max_history: int = 10000" in psrc:
        pbak = PROMPTS + ".bak-maxhist-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(PROMPTS, pbak)
        psrc = psrc.replace("max_history: int = 10000", "max_history: int = 200000", 1)
        with open(PROMPTS, "w", encoding="utf-8") as handle:
            handle.write(psrc)
        res = subprocess.run([sys.executable, "-m", "py_compile", PROMPTS],
                             capture_output=True, text=True)
        if res.returncode != 0:
            shutil.copy2(pbak, PROMPTS)
            print("  ✗ prompts_v2 编译失败已还原")
            return 1
        print("  ✓ build_messages 的 max_history 提到 20 万（等于不限制）")
        changed = True

    if changed:
        subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
        time.sleep(20)
        st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                            capture_output=True, text=True).stdout.strip()
        print("  astrbot-186:", st or "unknown")
        out = subprocess.run(["grep", "-n", "FORGET_TOKEN_LIMIT", MAIN],
                             capture_output=True, text=True).stdout
        print("  核对：", out.strip()[:120])
    return 0


if __name__ == "__main__":
    sys.exit(main())
