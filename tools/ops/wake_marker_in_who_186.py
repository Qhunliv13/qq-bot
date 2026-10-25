#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""唤醒人标记（带事实条数）注入到 _who()（2026-10-25）。

之前那次想改消息行，锚点没匹配上 ✗。这次直接改 _who()：
  · 用 QQ 号判定（昵称改了也不影响）
  · 只在该群唤醒人名单里才加标记
  · 标记形如：【唤醒人 QQ 1454594675 · 我记了他 3 条】
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

MAIN = "/root/astrbot-src/data/plugins/astrbot_plugin_higan/main.py"

OLD = '''        role = "主人" if qq in OWNER_QQS else "群友"
        return "%s（%s，QQ %s）" % (name, role, qq)
'''

NEW = '''        role = "主人" if qq in OWNER_QQS else "群友"
        base = "%s（%s，QQ %s）" % (name, role, qq)
        # [wake-marker] 只按 QQ 号判定该群唤醒人，带"我记了他几条"（2026-10-25）
        try:
            import json as _j
            from pathlib import Path as _P
            _conf = _j.loads(_P("/root/higan/groups.json").read_text(encoding="utf-8"))
            _gid = str(event.get_group_id() or "")
            _wl = [_ for _v in ((_conf.get("wake_persons") or {}).get(_gid) or [])
                   for _ in [str(_v)]]
            if qq in _wl:
                _cnt = 0
                try:
                    import importlib.util as _ilu
                    _sp = _ilu.spec_from_file_location(
                        "wpcnt", str(_P(__file__).resolve().parent / "memory.py"))
                    _m = _ilu.module_from_spec(_sp)
                    _sp.loader.exec_module(_m)
                    _cnt = _m.WakePersonFacts(
                        "/root/higan/memory/wake_persons.json").load().count(qq)
                except Exception:  # noqa: BLE001
                    _cnt = 0
                return "【唤醒人 QQ %s · 我记了他 %d 条】%s" % (qq, _cnt, base)
        except Exception:  # noqa: BLE001
            pass
        return base
'''


def main() -> int:
    """执行。"""
    src = open(MAIN, encoding="utf-8").read()
    if "wake-marker" in src:
        print("  - 标记已存在")
        return 0
    if OLD not in src:
        print("  ✗ _who 锚点没找到")
        return 1
    bak = MAIN + ".bak-who-" + time.strftime("%Y%m%d-%H%M%S")
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
    print("  ✓ 唤醒人标记已注入 _who（备份 %s）" % os.path.basename(bak))

    subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
    time.sleep(22)
    st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                        capture_output=True, text=True).stdout.strip()
    print("  astrbot-186:", st or "unknown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
