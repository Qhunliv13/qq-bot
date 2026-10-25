#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在唯一发送出口 _send 里拦掉工具调用语法（2026-10-25）。

现象：她把 `<||DSML|| calls><||DSML|| invoke name="wp_fact_add">…` 当正文发到群里
原因：_strip_tool_syntax 定义了但只挂在一条返回路径上；工具通路的最终发送没经过它 ✗
修法：在 _send 入口统一清洗；清洗后为空 ⇒ 直接不发（并记日志）。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time

MAIN = "/root/astrbot-src/data/plugins/astrbot_plugin_higan/main.py"

OLD = '''        # [recall] 2026-10-19: 她的回复里带 [撤回] ⇒ 先撤掉上一条再发新内容
        if "[撤回]" in reply:'''

NEW = '''        # [strip-at-send] 发送前统一清洗：工具调用语法/DSML 一律不发到群里
        try:
            _clean_reply = self._strip_tool_syntax(reply)
            if not _clean_reply.strip():
                logger.info("[higan] 回复只剩工具语法，已拦下不发送")
                return
            reply = _clean_reply
            # 兜底：还有 <||DSML|| 残留就整行丢掉
            if "DSML" in reply:
                reply = "\\n".join(
                    ln for ln in reply.splitlines() if "DSML" not in ln).strip()
                if not reply:
                    logger.info("[higan] 清洗后为空，不发送")
                    return
        except Exception as _sexc:  # noqa: BLE001
            logger.warning("[higan] 发送前清洗失败：%r", str(_sexc)[:100])

        # [recall] 2026-10-19: 她的回复里带 [撤回] ⇒ 先撤掉上一条再发新内容
        if "[撤回]" in reply:'''


def main() -> int:
    """执行。"""
    src = open(MAIN, encoding="utf-8").read()
    if "strip-at-send" in src:
        print("  - 已加过")
        return 0
    if OLD not in src:
        print("  ✗ 锚点不匹配")
        return 1
    bak = MAIN + ".bak-sendstrip-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(MAIN, bak)
    src = src.replace(OLD, NEW, 1)
    with open(MAIN, "w", encoding="utf-8") as handle:
        handle.write(src)
    res = subprocess.run([sys.executable, "-m", "py_compile", MAIN],
                         capture_output=True, text=True)
    if res.returncode != 0:
        shutil.copy2(bak, MAIN)
        print("  ✗ 编译失败已还原：", res.stderr[-300:])
        return 1
    print("  ✓ 发送前清洗已加（备份 %s）" % os.path.basename(bak))
    subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
    time.sleep(20)
    st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                        capture_output=True, text=True).stdout.strip()
    print("  astrbot-186:", st or "unknown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
