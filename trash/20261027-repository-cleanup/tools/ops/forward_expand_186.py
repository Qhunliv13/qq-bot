#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""加"主动展开转发内容"工具 + 标记里带 id（2026-10-25 用户要求）。

marker 改为：[转发聊天记录: 标题（N 条）id=xxx]
工具：forward_expand(id=… 或 msg_id=…)
   · 有 id 直接 get_forward_msg
   · 只有 msg_id 时先 get_msg 找到转发段里的 id
   · 返回前 100 条：{from, text}
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

MAIN = "/root/astrbot-src/data/plugins/astrbot_plugin_higan/main.py"

OLD_MARK = '''                                    parts.append("[转发聊天记录%s%s%s]" % (
                                        (": " + _title) if _title else "",
                                        ("（%d 条）" % _cnt) if _cnt else "",
                                        (" 含: " + "/".join(_names)) if _names else ""))'''

NEW_MARK = '''                                    _fid = str(sd.get("id") or sd.get("res_id")
                                                or sd.get("file") or "").strip()
                                    parts.append("[转发聊天记录%s%s%s%s]" % (
                                        (": " + _title) if _title else "",
                                        ("（%d 条）" % _cnt) if _cnt else "",
                                        (" id=" + _fid) if _fid else "",
                                        (" 含: " + "/".join(_names)) if _names else ""))'''

SCHEMA_ANCHOR = '            _f("history_search",'
SCHEMA_ADD = '''            _f("forward_expand", "展开一条合并转发，逐条读出谁说了什么"
               "（用上下文里 [转发聊天记录 … id=xxx] 的 id，或直接给消息号）",
               {"id": {"type": "string"}, "msg_id": {"type": "string"}}, []),
'''

DISPATCH_ANCHOR = '        if name == "history_search":'
DISPATCH_ADD = '''        if name == "forward_expand":
            # [forward-expand] 主动展开合并转发（逐条内容）
            try:
                _api = event.bot.api
                _fid = str(args.get("id") or "").strip()
                _mid = str(args.get("msg_id") or "").strip().lstrip("#")
                if not _fid and _mid:
                    _r = await _api.call_action("get_msg", message_id=int(_mid))
                    for _seg in ((_r or {}).get("message") or []):
                        _sd = _seg.get("data") or {}
                        if _seg.get("type") in ("forward", "node", "nodes"):
                            _fid = str(_sd.get("id") or _sd.get("res_id") or "").strip()
                            break
                if not _fid:
                    return {"ok": False, "error": "没有拿到转发 id（可传 msg_id 让我自己找）"}
                _res = await _api.call_action("get_forward_msg", id=_fid)
                _items = []
                for _node in ((_res or {}).get("messages") or [])[:100]:
                    _sender = (_node.get("sender") or {})
                    _nm = str(_sender.get("nickname") or _sender.get("card")
                              or _sender.get("user_id") or "").strip()
                    _txt = []
                    for _seg in (_node.get("message") or _node.get("content") or []):
                        if isinstance(_seg, dict):
                            _t = _seg.get("type")
                            _d = _seg.get("data") or {}
                            if _t == "text":
                                _txt.append(str(_d.get("text") or ""))
                            elif _t == "image":
                                _txt.append("[图片]")
                            elif _t == "at":
                                _txt.append("@" + str(_d.get("qq") or ""))
                            elif _t == "face":
                                _txt.append("[表情]")
                    _line = " ".join("".join(_txt).split())[:200]
                    if _line or _nm:
                        _items.append({"from": _nm, "text": _line})
                return {"ok": True, "count": len(_items), "items": _items}
            except Exception as _exc:  # noqa: BLE001
                logger.warning("[higan] 转发展开失败：%r", str(_exc)[:150])
                return {"ok": False, "error": str(_exc)[:150]}

'''


def main() -> int:
    """执行。"""
    src = open(MAIN, encoding="utf-8").read()
    changed = False
    bak = MAIN + ".bak-fwdexp-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(MAIN, bak)

    if " id=" not in src.split("转发聊天记录")[1][:400] if "转发聊天记录" in src else True:
        pass  # 占位，避免复杂表达式

    if OLD_MARK in src:
        src = src.replace(OLD_MARK, NEW_MARK, 1)
        changed = True
        print("  ✓ 转发标记里带上 id")

    if '"forward_expand"' not in src:
        pos = src.index(SCHEMA_ANCHOR)
        start = src.rindex("\n", 0, pos) + 1
        src = src[:start] + SCHEMA_ADD + src[start:]
        pos = src.index(DISPATCH_ANCHOR)
        start = src.rindex("\n", 0, pos) + 1
        src = src[:start] + DISPATCH_ADD + src[start:]
        changed = True
        print("  ✓ 新增工具 forward_expand")

    if not changed:
        return 0

    with open(MAIN, "w", encoding="utf-8") as handle:
        handle.write(src)
    res = subprocess.run([sys.executable, "-m", "py_compile", MAIN],
                         capture_output=True, text=True)
    if res.returncode != 0:
        shutil.copy2(bak, MAIN)
        print("  ✗ 编译失败已还原：", res.stderr[-400:])
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
