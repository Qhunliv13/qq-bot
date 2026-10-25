#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修"工具结果带跑题"：回话要针对对方问的那句，工具结果只当参考（2026-10-25）。

现象（用户截图）：瑞禾问"你是不是不喜欢我啊"，
她回"关于你只记了一条喵：别喊全名，叫瑞禾" ⇒ 完全答非所问
原因：工具结果回喂后，我给的那句提示把注意力引到了"工具返回的内容"上
修法：把最后一句改成"参考即可，回答她刚才那句话本身"
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

MAIN = "/root/astrbot-src/data/plugins/astrbot_plugin_higan/main.py"

OLD = '''            _msgs.append({"role": "user",
                          "content": "（以上是刚才工具返回的真实结果。"
                                     "现在用你自己的话回她，别报工具名和字段名。）"})'''

NEW = '''            _msgs.append({"role": "user",
                          "content": "（上面是工具返回的资料，仅供参考。"
                                     "现在回她刚才那句话本身：她问什么你就答什么，"
                                     "别把资料当话题，也别报工具名和字段名。）"})'''


def main() -> int:
    """执行。"""
    src = open(MAIN, encoding="utf-8").read()
    if OLD not in src:
        print("  ✗ 找不到提示行（可能已改）")
        return 0
    bak = MAIN + ".bak-focus-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(MAIN, bak)
    src = src.replace(OLD, NEW, 1)
    with open(MAIN, "w", encoding="utf-8") as handle:
        handle.write(src)
    res = subprocess.run([sys.executable, "-m", "py_compile", MAIN],
                         capture_output=True, text=True)
    if res.returncode != 0:
        shutil.copy2(bak, MAIN)
        print("  ✗ 编译失败已还原：", res.stderr[-200:])
        return 1
    print("  ✓ 回话聚焦已改（备份 %s）" % os.path.basename(bak))
    subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
    time.sleep(20)
    st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                        capture_output=True, text=True).stdout.strip()
    print("  astrbot-186:", st or "unknown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
