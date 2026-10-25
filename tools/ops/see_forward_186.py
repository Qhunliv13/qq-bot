#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""让她"看得见"转发的聊天记录 / 卡片消息（2026-10-25 用户要求）。

现状：合并转发（forward/node）、json/xml 卡片只落到 `[forward]` 这种裸标记 ✗
修法：渲染成带信息的中文标记：
  [转发聊天记录：<预览标题>（N 条）]
  [卡片消息：<标题/摘要>]
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

MAIN = "/root/astrbot-src/data/plugins/astrbot_plugin_higan/main.py"

ANCHOR = '''                                elif st in ("file", "record", "video", "audio"):'''

NEW = '''                                elif st in ("forward", "node", "nodes", "multimsg"):
                                    # [see-forward] 合并转发：给出标题/条数，别只留裸标记
                                    _title = str(sd.get("title") or sd.get("summary")
                                                 or sd.get("prompt") or "").strip()[:60]
                                    _cnt = 0
                                    for _k in ("content", "messages", "nodes"):
                                        _v = sd.get(_k)
                                        if isinstance(_v, list):
                                            _cnt = len(_v)
                                            break
                                    _names = []
                                    for _item in (sd.get("content") or sd.get("messages")
                                                  or [])[:5]:
                                        if isinstance(_item, dict):
                                            _snd = (_item.get("sender") or {})
                                            _nm = str((_snd or {}).get("nickname")
                                                      or _item.get("name") or "").strip()
                                            if _nm:
                                                _names.append(_nm)
                                    parts.append("[转发聊天记录%s%s%s]" % (
                                        (": " + _title) if _title else "",
                                        ("（%d 条）" % _cnt) if _cnt else "",
                                        (" 含: " + "/".join(_names)) if _names else ""))
                                elif st in ("json", "xml", "card", "lightapp"):
                                    # [see-card] 小程序/网页卡片：给出标题或摘要
                                    _raw = str(sd.get("data") or sd.get("content") or "")
                                    _ttl = ""
                                    for _key in ('"title"', '"desc"', '"prompt"', '"summary"'):
                                        _i = _raw.find(_key)
                                        if _i >= 0:
                                            _seg = _raw[_i:_i + 120]
                                            _q = _seg.find(":", len(_key))
                                            if _q > 0:
                                                _ttl = _seg[_q + 1:].strip(' ",}')[:60]
                                                break
                                    parts.append("[卡片消息%s]" % ((": " + _ttl) if _ttl else ""))
                                elif st in ("file", "record", "video", "audio"):'''

# 插到 file/record 分支之前（保持 elif 结构）


def main() -> int:
    """执行。"""
    src = open(MAIN, encoding="utf-8").read()
    if "see-forward" in src:
        print("  - 已加过")
        return 0
    if ANCHOR not in src:
        print("  ✗ 锚点不匹配")
        return 1
    bak = MAIN + ".bak-forward-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(MAIN, bak)
    src = src.replace(ANCHOR, NEW, 1)
    with open(MAIN, "w", encoding="utf-8") as handle:
        handle.write(src)
    res = subprocess.run([sys.executable, "-m", "py_compile", MAIN],
                         capture_output=True, text=True)
    if res.returncode != 0:
        shutil.copy2(bak, MAIN)
        print("  ✗ 编译失败已还原：", res.stderr[-300:])
        return 1
    print("  ✓ 转发/卡片消息可见（备份 %s）" % os.path.basename(bak))
    subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
    time.sleep(20)
    st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                        capture_output=True, text=True).stdout.strip()
    print("  astrbot-186:", st or "unknown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
