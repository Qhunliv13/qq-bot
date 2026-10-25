#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""唤醒人标记带"相关事实条数" + 把通用事实移入某人记录（2026-10-25）。

用户要求：
  · 遇到唤醒人时，标记里再带上"我记了他多少条相关事实"，她可主动请求查看
  · 增加：把某条通用事实移进某个唤醒人的记录里
  · 她能直接看到 QQ 号，不需要昵称反查（所以不做反查）
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

MAIN = "/root/astrbot-src/data/plugins/astrbot_plugin_higan/main.py"
BASE = "/root/astrbot-src/data/plugins/astrbot_plugin_higan"

OLD_MARK = '''            _mark = ""
            try:
                _wp_all = json.load(open("/root/higan/groups.json",
                                         encoding="utf-8")).get("wake_persons") or {}
                _sender_qq = str(event.get_sender_id() or "")
                if _sender_qq and _sender_qq in [str(x) for x in
                                                 (_wp_all.get(str(group)) or [])]:
                    _mark = "【唤醒人 QQ %s】" % _sender_qq
            except Exception:  # noqa: BLE001
                _mark = ""
'''

NEW_MARK = '''            _mark = ""
            try:
                _wp_all = json.load(open("/root/higan/groups.json",
                                         encoding="utf-8")).get("wake_persons") or {}
                _sender_qq = str(event.get_sender_id() or "")
                if _sender_qq and _sender_qq in [str(x) for x in
                                                 (_wp_all.get(str(group)) or [])]:
                    _cnt = 0
                    try:
                        import importlib.util as _ilu4
                        from pathlib import Path as _P4
                        _sp4 = _ilu4.spec_from_file_location(
                            "wpc", str(_P4(BASE_DIR, "memory.py")))
                        _wpc = _ilu4.module_from_spec(_sp4)
                        _sp4.loader.exec_module(_wpc)
                        _cnt = _wpc.WakePersonFacts(
                            "/root/higan/memory/wake_persons.json").load().count(_sender_qq)
                    except Exception:  # noqa: BLE001
                        _cnt = 0
                    _mark = "【唤醒人 QQ %s · 我记了他 %d 条】" % (_sender_qq, _cnt)
            except Exception:  # noqa: BLE001
                _mark = ""
'''

SCHEMA_ANCHOR = '            _f("wp_fact_add",'
SCHEMA_ADD = '''            _f("wp_fact_move", "把一条通用事实移进某个唤醒人的记录（会从通用里删掉）",
               {"id": {"type": "string"}, "qq": {"type": "string"}}, ["id", "qq"]),
'''

DISPATCH_ANCHOR = '        if name in ("wp_fact_add", "wp_fact_list", "wp_fact_search", "wp_fact_delete"):'
DISPATCH_ADD = '''        if name == "wp_fact_move":
            # [wp-move] 通用事实 → 某个唤醒人的记录（原子：先写成功再从通用里删）
            try:
                import importlib.util as _ilu5
                from pathlib import Path as _P5
                _sp5 = _ilu5.spec_from_file_location("wpm", str(_P5(BASE_DIR, "memory.py")))
                _wpmod5 = _ilu5.module_from_spec(_sp5)
                _sp5.loader.exec_module(_wpmod5)
                _qq5 = str(args.get("qq") or "").strip()
                _fid5 = str(args.get("id") or "").strip()
                if not _qq5 or not _fid5:
                    return {"ok": False, "error": "要同时给 id 和 qq"}
                _gm = _wpmod5.Memory("/root/higan/memory/memory.json").load()
                _hit = [f for f in _gm.list_facts() if str(f.get("id")) == _fid5]
                if not _hit:
                    return {"ok": False, "error": "通用事实里没有 %s" % _fid5}
                _text = str(_hit[0].get("text") or "")
                _st5 = _wpmod5.WakePersonFacts(
                    "/root/higan/memory/wake_persons.json").load()
                _new_id = _st5.add(_qq5, _text)
                if not _new_id:
                    return {"ok": False, "error": "写入唤醒人记录失败"}
                _gm.delete_fact(_fid5)
                logger.info("[higan] 通用事实 %s 已移入唤醒人 %s（%s）" % (_fid5, _qq5, _new_id))
                return {"ok": True, "moved": _fid5, "to_person": _qq5,
                        "new_id": _new_id, "count": _st5.count(_qq5)}
            except Exception as _exc:  # noqa: BLE001
                return {"ok": False, "error": str(_exc)[:150]}

'''


def main() -> int:
    """执行。"""
    src = open(MAIN, encoding="utf-8").read()
    changed = False
    bak = MAIN + ".bak-mark-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(MAIN, bak)

    if OLD_MARK in src:
        src = src.replace(OLD_MARK, NEW_MARK, 1)
        changed = True
        print("  ✓ 唤醒人标记加事实条数")
    else:
        print("  - 标记已是新版或未找到")

    if '"wp_fact_move"' not in src:
        pos = src.index(SCHEMA_ANCHOR)
        start = src.rindex("\n", 0, pos) + 1
        src = src[:start] + SCHEMA_ADD + src[start:]
        pos = src.index(DISPATCH_ANCHOR)
        start = src.rindex("\n", 0, pos) + 1
        src = src[:start] + DISPATCH_ADD + src[start:]
        changed = True
        print("  ✓ 新增工具 wp_fact_move")

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
    print("  ✓ 编译通过（%d 字节，备份 %s）"
          % (os.path.getsize(MAIN), os.path.basename(bak)))

    subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
    time.sleep(22)
    st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                        capture_output=True, text=True).stdout.strip()
    print("  astrbot-186:", st or "unknown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
