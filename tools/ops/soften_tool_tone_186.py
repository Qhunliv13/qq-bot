#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""淡化"工具能力"在人格里的存在感（2026-10-25 用户：截屏跑脚本太强调了）。

现状（DECISION_SYSTEM 里）：
  # 你会用工具
  你有身体：能截屏、开网页、点击/打字、跑脚本、读写文件、查自己的聊天记录，也能给自己排后台任务。
  该动手就动手；做完用一句话说清结果。截图、报错这些是内部信息，别人要看再发。
⇒ 她说"我可是会截屏跑脚本的猫喵" —— 明显被这段带偏 ✗

改法：压成一句中性的，不列举能力、不鼓励炫耀。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time

PROMPTS = "/root/astrbot-src/data/plugins/astrbot_plugin_higan/prompts_v2.py"

NEW_BLOCK = '''# 需要的时候你手边有工具
- 平时就像在群里聊天，不用刻意提自己会什么。
- 真需要（查旧消息、看某个东西、动手做事）再用工具，做完简单说一句就行。
'''


def main() -> int:
    """执行。"""
    src = open(PROMPTS, encoding="utf-8").read()
    if "需要的时候你手边有工具" in src:
        print("  - 已改过")
        return 0
    mt = re.search(r'# 你会用工具\n[\s\S]*?(?=\n# |\Z)', src)
    if not mt:
        print("  ✗ 找不到『你会用工具』那段")
        return 1
    bak = PROMPTS + ".bak-tooltone-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(PROMPTS, bak)
    src = src[:mt.start()] + NEW_BLOCK + src[mt.end():]
    with open(PROMPTS, "w", encoding="utf-8") as handle:
        handle.write(src)
    res = subprocess.run([sys.executable, "-m", "py_compile", PROMPTS],
                         capture_output=True, text=True)
    if res.returncode != 0:
        shutil.copy2(bak, PROMPTS)
        print("  ✗ 编译失败已还原：", res.stderr[-300:])
        return 1
    print("  ✓ 已淡化工具描述（备份 %s）" % os.path.basename(bak))

    # 核对
    text = open(PROMPTS, encoding="utf-8").read()
    seg = re.search(r'# 需要的时候你手边有工具[\s\S]{0,160}', text)
    print("  现在这段：")
    for line in (seg.group(0) if seg else "").splitlines()[:4]:
        print("   ", line)

    subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
    time.sleep(20)
    st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                        capture_output=True, text=True).stdout.strip()
    print("  astrbot-186:", st or "unknown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
