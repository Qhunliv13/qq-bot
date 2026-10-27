#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""她复读别人的话就不发（2026-10-25 用户截图：她把别家 bot 的报错原样发了一遍）。

现象：群里"酒狐"发了「您(ID: 3010160376)的权限不足以使用此指令…」，她原样复读 ✗
修法：发送前和最近上下文比对，相似度 ≥0.85 且来源不是她自己 ⇒ 丢弃并记日志
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

MAIN = "/root/astrbot-src/data/plugins/astrbot_plugin_higan/main.py"

OLD = '''        # [media-dedupe] 同一张图 120 秒内不重复发（插件投递 + 显式 send_shot 会撞车）'''

NEW = '''        # [no-parrot] 别复读别人刚说过的话（相似度高就丢掉）
        try:
            import difflib as _dl
            _body = str(reply or "").split("[发图")[0].strip()
            _hist = list(self._recent.get(str(group), []))[-6:]
            for _ln in reversed(_hist):
                _txt = _ln.split("：", 1)[-1].strip()
                if not _txt or _txt.startswith("你（彼岸酱）"):
                    continue
                _ratio = _dl.SequenceMatcher(None, _body, _txt).ratio()
                if len(_body) >= 8 and _ratio >= 0.85:
                    logger.info("[higan] 回复几乎在复读别人（相似度 %.2f）⇒ 丢弃" % _ratio)
                    return
        except Exception as _pexc:  # noqa: BLE001
            logger.warning("[higan] 复读检测异常：%r", str(_pexc)[:100])

        # [media-dedupe] 同一张图 120 秒内不重复发（插件投递 + 显式 send_shot 会撞车）'''


def main() -> int:
    """执行。"""
    src = open(MAIN, encoding="utf-8").read()
    if "no-parrot" in src:
        print("  - 已加过")
        return 0
    if OLD not in src:
        print("  ✗ 锚点不匹配")
        return 1
    bak = MAIN + ".bak-parrot-" + time.strftime("%Y%m%d-%H%M%S")
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
    print("  ✓ 复读拦截已加（备份 %s）" % os.path.basename(bak))
    subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
    time.sleep(20)
    st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                        capture_output=True, text=True).stdout.strip()
    print("  astrbot-186:", st or "unknown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
