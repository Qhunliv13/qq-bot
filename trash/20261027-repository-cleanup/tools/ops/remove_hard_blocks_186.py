#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""删掉硬拦截：复读检测 + 媒体去重（2026-10-25 用户明确要求）。

用户指出：那条复读是 **NapCat 的硬编码行为**，不是模型复读 ⇒ 我们的硬拦截是错的 ✗
media 去重同样是硬拦截 ⇒ 一并删除。

保留说明：DSML 语法清洗也属于硬拦截，但那是协议垃圾（发出去就是乱码），
用户没点名，先留着，等他说话再动。
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
    bak = MAIN + ".bak-nohard-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(MAIN, bak)
    changed = False

    # ① 删复读检测块
    pat1 = re.compile(r"        # \[no-parrot\][\s\S]*?            logger\.warning\(\"\[higan\] 复读检测异常：%r\", str\(_pexc\)\[:100\]\)\n\n")
    if pat1.search(src):
        src = pat1.sub("", src, count=1)
        changed = True
        print("  ✓ 已删除复读拦截")

    # ② 删媒体去重块
    pat2 = re.compile(r"        # \[media-dedupe\][\s\S]*?            logger\.warning\(\"\[higan\] 媒体去重异常：%r\", str\(_mexc\)\[:100\]\)\n\n")
    if pat2.search(src):
        src = pat2.sub("", src, count=1)
        changed = True
        print("  ✓ 已删除媒体去重")

    if not changed:
        print("  - 没找到要删的块（可能已删）")
        return 0

    with open(MAIN, "w", encoding="utf-8") as handle:
        handle.write(src)
    res = subprocess.run([sys.executable, "-m", "py_compile", MAIN],
                         capture_output=True, text=True)
    if res.returncode != 0:
        shutil.copy2(bak, MAIN)
        print("  ✗ 编译失败已还原：", res.stderr[-400:])
        return 1
    print("  ✓ 编译通过（备份 %s）" % os.path.basename(bak))
    subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
    time.sleep(20)
    st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                        capture_output=True, text=True).stdout.strip()
    print("  astrbot-186:", st or "unknown")
    cnt = len(re.findall(r"no-parrot|media-dedupe", open(MAIN, encoding="utf-8").read()))
    print("  残留标记数:", cnt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
