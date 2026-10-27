#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修 BASE_DIR 用到了未导入的 os（2026-10-25）。

日志：NameError: name 'os' is not defined
原因：我把 BASE_DIR 插在 from __future__ 之后，但那里还没有 import os
修法：把 BASE_DIR 改成不依赖 os 的写法（用 __file__ 直接切），或在其上方补 import os
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

MAIN = "/root/astrbot-src/data/plugins/astrbot_plugin_higan/main.py"

OLD = "BASE_DIR = os.path.dirname(os.path.abspath(__file__))"
NEW = ('BASE_DIR = str(__import__("pathlib").Path(__file__).resolve().parent)'
       '  # [basedir] 不依赖 os 是否已导入')


def main() -> int:
    """执行。"""
    src = open(MAIN, encoding="utf-8").read()
    if NEW in src:
        print("  - 已是安全写法")
        return 0
    if OLD not in src:
        print("  ✗ 找不到 BASE_DIR 行")
        return 1
    bak = MAIN + ".bak-basefix-" + time.strftime("%Y%m%d-%H%M%S")
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
    print("  ✓ BASE_DIR 改为安全写法（备份 %s）" % os.path.basename(bak))
    subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
    time.sleep(20)
    st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                        capture_output=True, text=True).stdout.strip()
    print("  astrbot-186:", st or "unknown")
    log = subprocess.run(
        ["journalctl", "-u", "astrbot-186", "--since", "-2min", "--no-pager"],
        capture_output=True, text=True).stdout
    bad = [ln for ln in log.splitlines() if "NameError" in ln or "Traceback" in ln]
    print("  近 2 分钟异常行数:", len(bad))
    for ln in bad[:3]:
        print("   ", ln[-140:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
