#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""同一条消息只回一次（2026-10-25 用户：一句话回了两遍）。

现象：一条 @ 消息既走了工具通路（她回"谢谢你喵，下次我好好记着"），
      又走了普通通路（"好呀，明天再认识酒狐喵"）⇒ 两条 ✗
修法：在唯一的发送出口 _send 里按 (群, 消息ID) 做 5 秒窗口去重；
      重复的直接丢弃并记日志（不打断流程）。
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
    if "_reply_dedupe" in src:
        print("  - 已有去重")
        return 0

    # 找到 _send 定义体（async def _send）
    mt = re.search(r"(\n    async def _send\([^)]*\)[^\n]*:\n)((?:\s{8,}[^\n]*\n)+)", src)
    if not mt:
        print("  ✗ 找不到 _send 定义")
        return 1
    head, body = mt.group(1), mt.group(2)
    guard = (
        '        # [reply-dedupe] 同一条消息 5 秒内只回一次（工具通路+普通通路会撞车）\n'
        '        try:\n'
        '            _mid = str(getattr(getattr(event, "message_obj", None),\n'
        '                               "message_id", "") or "")\n'
        '            _key = (str(group), _mid)\n'
        '            _now = time.time()\n'
        '            _seen = getattr(self, "_reply_dedupe", None)\n'
        '            if _seen is None:\n'
        '                _seen = {}\n'
        '                self._reply_dedupe = _seen\n'
        '            _last = _seen.get(_key, 0)\n'
        '            if _mid and _now - _last < 5:\n'
        '                logger.info("[higan] 这条消息刚回过了，丢弃重复回复")\n'
        '                return\n'
        '            if _mid:\n'
        '                _seen[_key] = _now\n'
        '                if len(_seen) > 200:\n'
        '                    for _k in list(_seen)[:100]:\n'
        '                        _seen.pop(_k, None)\n'
        '        except Exception:  # noqa: BLE001\n'
        '            pass\n'
    )
    src = src[:mt.start(2)] + guard + body + src[mt.end(2):]

    bak = MAIN + ".bak-dedupe-reply-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(MAIN, bak)
    with open(MAIN, "w", encoding="utf-8") as handle:
        handle.write(src)
    res = subprocess.run([sys.executable, "-m", "py_compile", MAIN],
                         capture_output=True, text=True)
    if res.returncode != 0:
        shutil.copy2(bak, MAIN)
        print("  ✗ 编译失败已还原：", res.stderr[-300:])
        return 1
    print("  ✓ _send 增加 5 秒去重（备份 %s）" % os.path.basename(bak))
    subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
    time.sleep(20)
    st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                        capture_output=True, text=True).stdout.strip()
    print("  astrbot-186:", st or "unknown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
