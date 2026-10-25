#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""同一张图 2 分钟内不重复发（2026-10-25 用户：截图发了两遍）。

只对**媒体**去重（按文件路径 + 120 秒窗口），不影响文字回复。
原因：她一次调用可能既走"插件投递"又显式 send_shot ⇒ 同一路径发两次 ✗
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time

MAIN = "/root/astrbot-src/data/plugins/astrbot_plugin_higan/main.py"

OLD = '''        # [strip-at-send] 发送前统一清洗：工具调用语法/DSML 一律不发到群里'''

NEW = '''        # [media-dedupe] 同一张图 120 秒内不重复发（插件投递 + 显式 send_shot 会撞车）
        try:
            _paths = re.findall(r"\\[发图:([^\\]]+)\\]", str(reply or ""))
            if _paths:
                _sent = getattr(self, "_sent_media", None)
                if _sent is None:
                    _sent = {}
                    self._sent_media = _sent
                _now = time.time()
                for _p in _paths:
                    _key = str(_p).strip()
                    if _now - float(_sent.get(_key) or 0) < 120:
                        logger.info("[higan] 这张图刚发过，丢弃重复：%s" % _key[:70])
                        reply = str(reply).replace("[发图:%s]" % _p, "").strip()
                    else:
                        _sent[_key] = _now
                if not str(reply or "").strip():
                    logger.info("[higan] 去重后没有内容可发")
                    return
                if len(_sent) > 300:
                    for _k in list(_sent)[:150]:
                        _sent.pop(_k, None)
        except Exception as _mexc:  # noqa: BLE001
            logger.warning("[higan] 媒体去重异常：%r", str(_mexc)[:100])

        # [strip-at-send] 发送前统一清洗：工具调用语法/DSML 一律不发到群里'''


def main() -> int:
    """执行。"""
    src = open(MAIN, encoding="utf-8").read()
    if "media-dedupe" in src:
        print("  - 已加过")
        return 0
    if OLD not in src:
        print("  ✗ 锚点不匹配")
        return 1
    bak = MAIN + ".bak-mediadup-" + time.strftime("%Y%m%d-%H%M%S")
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
    print("  ✓ 媒体去重已加（备份 %s）" % os.path.basename(bak))
    subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
    time.sleep(20)
    st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                        capture_output=True, text=True).stdout.strip()
    print("  astrbot-186:", st or "unknown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
