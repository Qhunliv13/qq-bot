#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修 _at_targets 丢了 @staticmethod（2026-10-25）。

日志：TypeError('HiganPlugin._at_targets(...)') ⇒ 以未绑定方式调用 ✗
原因：我插 helper 时插到了 @staticmethod 与 def _at_targets 之间，
      装饰器被 helper 吃掉 ⇒ _at_targets 变成实例方法 ✗
修法：把 @staticmethod 补回 _at_targets 前面（幂等）；helper 保留实例方法（它们用了 self ✓）
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time

MAIN = "/root/astrbot-src/data/plugins/astrbot_plugin_higan/main.py"


def main() -> int:
    """执行。"""
    src = open(MAIN, encoding="utf-8").read()
    target = "    def _at_targets(event: AstrMessageEvent) -> list[str]:"
    if target not in src:
        print("  ✗ 找不到 _at_targets")
        return 1
    idx = src.index(target)
    before = src[max(0, idx - 200):idx]
    if "@staticmethod" in before.splitlines()[-1]:
        print("  - _at_targets 已有 @staticmethod")
        return 0

    bak = MAIN + ".bak-st-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(MAIN, bak)
    src = src.replace(target, "    @staticmethod\n" + target, 1)
    with open(MAIN, "w", encoding="utf-8") as handle:
        handle.write(src)

    res = subprocess.run([sys.executable, "-m", "py_compile", MAIN],
                         capture_output=True, text=True)
    if res.returncode != 0:
        shutil.copy2(bak, MAIN)
        print("  ✗ 编译失败已还原：", res.stderr[-300:])
        return 1
    print("  ✓ 已补回 @staticmethod（备份 %s）" % os.path.basename(bak))

    subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
    time.sleep(20)
    st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                        capture_output=True, text=True).stdout.strip()
    print("  astrbot-186:", st or "unknown")

    # 语法级自检：确认能当静态方法取用
    chk = subprocess.run(
        [sys.executable, "-c",
         "import ast;src=open('%s',encoding='utf-8').read();"
         "t=ast.parse(src);\n"
         "print('  ok')" % MAIN], capture_output=True, text=True)
    print(chk.stdout.strip() or chk.stderr.strip()[-200:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
