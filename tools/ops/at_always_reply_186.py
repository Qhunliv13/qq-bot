#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""@ 她 = 永远回（2026-10-25 用户要求）。

现状问题：门禁用 _is_woken(event) 判断，实测有 @ 消息仍是 woken=False ⇒ 被概率挡掉
修法：门禁再加一条硬判据 —— 本条 @ 段里出现她自己的 QQ ⇒ 直接算被叫到
      （用 _at_targets(event) 与 event.get_self_id() 比对，不依赖 is_at_or_wake_command）
同时在日志里标出来，便于核对生效情况。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time

MAIN = "/root/astrbot-src/data/plugins/astrbot_plugin_higan/main.py"

OLD = "                _woken_e = self._is_woken(event) or self._wake_hit(event, text)"

NEW = '''                # [at-always-reply] @ 她 就一定回：直接比对 @ 目标与自己的 QQ
                _self_qq = ""
                try:
                    _self_qq = str(event.get_self_id() or "")
                except Exception:  # noqa: BLE001
                    _self_qq = ""
                _at_targets_now = []
                try:
                    _at_targets_now = [str(x) for x in (self._at_targets(event) or [])]
                except Exception:  # noqa: BLE001
                    _at_targets_now = []
                _at_me_now = bool(_self_qq) and _self_qq in _at_targets_now
                _woken_e = (self._is_woken(event) or self._wake_hit(event, text)
                            or _at_me_now)
                if _at_me_now:
                    logger.info("[higan] 本条 @ 了她（self=%s）⇒ 直接进入回复判定"
                                % _self_qq)'''


def main() -> int:
    """执行。"""
    src = open(MAIN, encoding="utf-8").read()
    if "at-always-reply" in src:
        print("  - 已有 @ 必回逻辑")
        return 0
    if OLD not in src:
        print("  ✗ 找不到门禁那行")
        return 1
    bak = MAIN + ".bak-atme-" + time.strftime("%Y%m%d-%H%M%S")
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
    print("  ✓ @ 必回逻辑已加（备份 %s）" % os.path.basename(bak))
    subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
    time.sleep(20)
    st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                        capture_output=True, text=True).stdout.strip()
    print("  astrbot-186:", st or "unknown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
