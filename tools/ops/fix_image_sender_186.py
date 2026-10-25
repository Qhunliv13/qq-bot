#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修"图片不认人"（2026-10-25 用户现场：群友发的猪图，她当成主人发的）。

现象：有人发图，她回"主人这是要拱翻谁呀" ⇒ 把发言者当成了主人 ✗
原因：纯图片消息的文本只有 "[图片]"，她在生成时容易把"上一条主人说的话"当成本条来源 ✗
修法：纯图片/纯文件消息，在文本里直接写清来源：
      "（这是 <昵称>（身份，QQ号） 发的图片）"
      —— 只是数据标注，不是指令
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
    if "img-from" in src:
        print("  - 已加过")
        return 0

    # 找消息行构造：line = "%s%s：%s%s" % (self._who(event), _tag, text or "[图片]", ...)
    mt = re.search(r'(\n(\s+)line = "%s%s：%s%s" % \(self\._who\(event\), _tag, )'
                   r'(text or "\[图片\]")', src)
    if not mt:
        print("  ✗ 找不到消息行构造")
        return 1
    old = mt.group(0)
    indent = mt.group(2)
    new = (mt.group(1) +
           '((text or "") if str(text or "").strip() '
           'else ("（这是 %s 发的图片/文件）" % self._who(event)))')
    src = src.replace(old, new, 1)

    bak = MAIN + ".bak-imgfrom-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(MAIN, bak)
    with open(MAIN, "w", encoding="utf-8") as handle:
        handle.write(src)
    res = subprocess.run([sys.executable, "-m", "py_compile", MAIN],
                         capture_output=True, text=True)
    if res.returncode != 0:
        shutil.copy2(bak, MAIN)
        print("  ✗ 编译失败已还原：", res.stderr[-300:])
        return 1
    print("  ✓ 纯图片消息会写明来源（备份 %s）" % os.path.basename(bak))

    # 校验
    text = open(MAIN, encoding="utf-8").read()
    mt = re.search(r'line = "%s%s：%s%s" % \(self\._who\(event\), _tag, (.{0,90})', text)
    print("  现在构造：", (mt.group(1) if mt else "(未找到)")[:90])

    subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
    time.sleep(20)
    st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                        capture_output=True, text=True).stdout.strip()
    print("  astrbot-186:", st or "unknown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
