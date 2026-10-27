#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A：修"唤醒人读不到"（group 归一化 + 兼容多种键写法 + 命中日志）（2026-10-25）。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time

MAIN = "/root/astrbot-src/data/plugins/astrbot_plugin_higan/main.py"

OLD = '''        def _list_for(key: str) -> list:
            """取本群的名单（兼容旧的 list 写法）。"""
            val = conf.get(key) or {}
            if isinstance(val, dict):
                return [str(x) for x in (val.get(str(group)) or [])]
            return [str(x) for x in val]
'''

NEW = '''        _grp = str(group or "").strip()
        _grp_num = "".join(ch for ch in _grp if ch.isdigit())

        def _pick(table: dict) -> list:
            """按群号取名单：先精确，再按数字后缀匹配（兼容 int 键 / 前缀键）。"""
            if not isinstance(table, dict):
                return []
            for _k, _v in table.items():
                _ks = str(_k).strip()
                _kn = "".join(ch for ch in _ks if ch.isdigit())
                if _ks == _grp or (_kn and _kn == _grp_num) or (
                        _grp_num and _grp.endswith(_kn)):
                    return [str(x) for x in (_v or [])]
            return []

        def _list_for(key: str) -> list:
            """取本群的名单（兼容旧的 list 写法）。"""
            val = conf.get(key) or {}
            if isinstance(val, dict):
                return _pick(val)
            return [str(x) for x in val]
'''

OLD2 = '''        if sender and sender in _list_for("wake_persons"):
            return "person"'''

NEW2 = '''        _persons = _list_for("wake_persons")
        if _persons:
            logger.info("[higan] 唤醒人名单(group=%s)：%s ｜ 本条发言人=%s"
                        % (_grp, ",".join(_persons[:6]), sender or "(空)"))
        if sender and sender in _persons:
            return "person"'''


def main() -> int:
    """执行。"""
    src = open(MAIN, encoding="utf-8").read()
    if "_pick(table" in src:
        print("  - 已修过")
        return 0
    if OLD not in src or OLD2 not in src:
        print("  ✗ 锚点不匹配")
        return 1
    bak = MAIN + ".bak-wakefix-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(MAIN, bak)
    src = src.replace(OLD, NEW, 1).replace(OLD2, NEW2, 1)
    with open(MAIN, "w", encoding="utf-8") as handle:
        handle.write(src)
    res = subprocess.run([sys.executable, "-m", "py_compile", MAIN],
                         capture_output=True, text=True)
    if res.returncode != 0:
        shutil.copy2(bak, MAIN)
        print("  ✗ 编译失败已还原：", res.stderr[-300:])
        return 1
    print("  ✓ 唤醒人查找已归一化（备份 %s）" % os.path.basename(bak))
    subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
    time.sleep(20)
    st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                        capture_output=True, text=True).stdout.strip()
    print("  astrbot-186:", st or "unknown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
