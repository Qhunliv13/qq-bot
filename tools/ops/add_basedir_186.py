#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补 BASE_DIR 常量（wp_fact_* 依赖它，缺了就 NameError ⇒ 她报"工具出岔子"）（2026-10-25）。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

MAIN = "/root/astrbot-src/data/plugins/astrbot_plugin_higan/main.py"


def main() -> int:
    """执行。"""
    src = open(MAIN, encoding="utf-8").read()
    if "\nBASE_DIR = " in src:
        print("  - BASE_DIR 已存在")
        return 0
    bak = MAIN + ".bak-basedir-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(MAIN, bak)
    marker = "from __future__ import annotations\n"
    src = src.replace(
        marker,
        marker + '\n# [basedir] 插件目录（wp_fact_* 按路径加载 memory.py 时用）\n'
                 'BASE_DIR = os.path.dirname(os.path.abspath(__file__))\n', 1)
    with open(MAIN, "w", encoding="utf-8") as handle:
        handle.write(src)
    res = subprocess.run([sys.executable, "-m", "py_compile", MAIN],
                         capture_output=True, text=True)
    if res.returncode != 0:
        shutil.copy2(bak, MAIN)
        print("  ✗ 编译失败已还原：", res.stderr[-300:])
        return 1
    print("  ✓ BASE_DIR 已补（备份 %s）" % os.path.basename(bak))
    subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
    time.sleep(20)
    st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                        capture_output=True, text=True).stdout.strip()
    print("  astrbot-186:", st or "unknown")
    out = subprocess.run(["grep", "-c", "BASE_DIR = ", MAIN], capture_output=True, text=True)
    print("  校验 BASE_DIR 行数:", out.stdout.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
