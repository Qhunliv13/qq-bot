#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新增 wake_person_info(qq)：一次看某唤醒人的全部信息（2026-10-25）。

返回：
  · 在哪些群被设为唤醒人
  · 专属配置（pct / words）
  · 该群/全局的定点查看时间（wake_at）
  · 他相关的事实条数与内容（sleep 200 上限内的全部）
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

MAIN = "/root/astrbot-src/data/plugins/astrbot_plugin_higan/main.py"

SCHEMA_ANCHOR = '            _f("wake_person_set",'
SCHEMA_ADD = '''            _f("wake_person_info", "一次看某个唤醒人的全部信息：在哪些群唤醒我、"
               "专属回复率/唤醒词、定点查看时间、我记了他什么", {"qq": {"type": "string"}}, ["qq"]),
'''

DISPATCH_ANCHOR = '        if name in ("wake_person_set", "wake_person_show"):'
DISPATCH_ADD = '''        if name == "wake_person_info":
            # [wake-person-info] 唤醒人全息视图（配置 + 事实 + 定点时间）
            try:
                with open("/root/higan/groups.json", encoding="utf-8") as _h:
                    _c = json.load(_h)
                _qq = str(args.get("qq") or "").strip()
                if not _qq:
                    return {"ok": False, "error": "要给 qq"}
                _groups = [str(_g) for _g, _lst in (_c.get("wake_persons") or {}).items()
                           if _qq in [str(x) for x in (_lst or [])]]
                _conf = (_c.get("wake_person_conf") or {}).get(_qq) or {}
                _at = _c.get("wake_at") or {}
                _facts = []
                try:
                    import importlib.util as _ilu6
                    from pathlib import Path as _P6
                    _sp6 = _ilu6.spec_from_file_location(
                        "wpi", str(_P6(BASE_DIR, "memory.py")))
                    _wpm = _ilu6.module_from_spec(_sp6)
                    _sp6.loader.exec_module(_wpm)
                    _st6 = _wpm.WakePersonFacts(
                        "/root/higan/memory/wake_persons.json").load()
                    _facts = _st6.list(_qq)
                except Exception:  # noqa: BLE001
                    _facts = []
                return {"ok": True, "qq": _qq,
                        "is_wake_person_in": _groups,
                        "conf": {"pct": _conf.get("pct", 100),
                                 "words": _conf.get("words") or []},
                        "wake_at": {g: (_at.get(g) or []) for g in _groups} or _at,
                        "facts_count": len(_facts),
                        "facts": _facts[:50]}
            except Exception as _exc:  # noqa: BLE001
                return {"ok": False, "error": str(_exc)[:150]}

'''


def main() -> int:
    """执行。"""
    src = open(MAIN, encoding="utf-8").read()
    changed = False
    bak = MAIN + ".bak-wpinfo-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(MAIN, bak)

    if '"wake_person_info"' not in src:
        pos = src.index(SCHEMA_ANCHOR)
        start = src.rindex("\n", 0, pos) + 1
        src = src[:start] + SCHEMA_ADD + src[start:]
        pos = src.index(DISPATCH_ANCHOR)
        start = src.rindex("\n", 0, pos) + 1
        src = src[:start] + DISPATCH_ADD + src[start:]
        changed = True
        print("  ✓ 新增工具 wake_person_info")

    if not changed:
        return 0

    with open(MAIN, "w", encoding="utf-8") as handle:
        handle.write(src)
    res = subprocess.run([sys.executable, "-m", "py_compile", MAIN],
                         capture_output=True, text=True)
    if res.returncode != 0:
        shutil.copy2(bak, MAIN)
        print("  ✗ 编译失败已还原：", res.stderr[-300:])
        return 1
    print("  ✓ 编译通过（备份 %s）" % os.path.basename(bak))
    subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
    time.sleep(20)
    st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                        capture_output=True, text=True).stdout.strip()
    print("  astrbot-186:", st or "unknown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
