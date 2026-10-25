#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工具结果回话时也要"用她的人格"（2026-10-25 用户：人设不牢、太人机）。

证据：工具结果回喂那段的 system 用的是 `system`（工具通路的说明），
      **没有人格** ⇒ 出来就是"我这边只留着一句…别的全空白"这种说明书腔 ✗
修法：这段的 system 改成 "人格 + 一句极短的回话要求"，其余不动。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

MAIN = "/root/astrbot-src/data/plugins/astrbot_plugin_higan/main.py"

OLD = '''            _msgs: list = [{"role": "system", "content": system}]'''
NEW = '''            # [persona-voice] 回话必须带着她的人格，别再拿工具说明当 system
            _persona_sys = (getattr(self, "persona", "") or "").strip()
            if _persona_sys:
                _persona_sys += ("\\n\\n（刚才你查了点资料；现在就像平时聊天那样回她，"
                                 "短一点、用你的语气，别念资料、别报工具。）")
            else:
                _persona_sys = system
            _msgs: list = [{"role": "system", "content": _persona_sys}]'''


def main() -> int:
    """执行。"""
    src = open(MAIN, encoding="utf-8").read()
    if "persona-voice" in src:
        print("  - 已改过")
        return 0
    if OLD not in src:
        print("  ✗ 找不到 system 行")
        return 1
    bak = MAIN + ".bak-voice-" + time.strftime("%Y%m%d-%H%M%S")
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
    print("  ✓ 工具回话改用她的人格作 system（备份 %s）" % os.path.basename(bak))
    subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
    time.sleep(20)
    st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                        capture_output=True, text=True).stdout.strip()
    print("  astrbot-186:", st or "unknown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
