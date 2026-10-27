#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""唤醒时间 = 定点定时看一眼群（不是时间区间）（2026-10-25 用户澄清）。

语义纠正：
  · 之前的 hours=[8,24] 是"时间段" ✗ 不对
  · 现在改为 wake_at：{"qq": ["09:00", "21:30"]} = 到点就主动看一遍群，
    无论有没有新消息；同一分钟只触发一次

实现：
  · 在已有的 _poll_loop（每 30 秒一轮）里，额外检查 wake_at
  · 到点 ⇒ 复用现有"定时查看"同一套逻辑（取最近 20 行 → 让她决定说不说 → 发）
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

MAIN = "/root/astrbot-src/data/plugins/astrbot_plugin_higan/main.py"

OLD = '''                logger.info("[higan] 定时查看：心跳一轮（poll=%d 个）" % len(poll))
                if not poll:
                    continue'''

NEW = '''                logger.info("[higan] 定时查看：心跳一轮（poll=%d 个）" % len(poll))

                # [wake-at] 定点看一眼：wake_at = {"<群号>": ["09:00", "21:30"]}
                try:
                    _at_tbl = conf.get("wake_at") or {}
                    _now_hm = time.strftime("%H:%M")
                    _fired = getattr(self, "_wake_at_fired", None)
                    if _fired is None:
                        _fired = {}
                        self._wake_at_fired = _fired
                    for _g, _times in list(_at_tbl.items()):
                        for _hm in (_times or []):
                            if str(_hm).strip() != _now_hm:
                                continue
                            _key = "%s@%s" % (_g, _now_hm)
                            if _fired.get(_key):
                                continue
                            _fired[_key] = True
                            logger.info("[higan] 定点查看：群 %s 到点 %s（无论有无新消息）"
                                        % (_g, _hm))
                            _lines = list(self._recent.get(str(_g), []))[-20:]
                            if not _lines:
                                logger.info("[higan] 定点查看：该群还没有历史，跳过")
                                continue
                            _prompt = ("【群%s 最近的聊天】\\n%s\\n\\n现在是定点查看时间："
                                       "要说话就正常说一句；没什么要说的就只输出 SILENT。"
                                       % (_g, "\\n".join(_lines)))
                            try:
                                _out = await self._llm_plain(
                                    "你是彼岸酱，正在群里。可以自己决定说不说", _prompt, [])
                            except Exception as _exc:  # noqa: BLE001
                                logger.warning("[higan] 定点查看决策失败：%r", str(_exc)[:100])
                                continue
                            _out = (_out or "").strip()
                            if not _out or _out.upper().strip("。.!！ ") == "SILENT":
                                logger.info("[higan] 定点查看后选择沉默")
                                continue
                            try:
                                await self._qq_send("group", _out[:800], 0, str(_g))
                                logger.info("[higan] 定点查看后发言：%s" % _out[:40])
                            except Exception as _exc:  # noqa: BLE001
                                logger.warning("[higan] 定点发言失败：%r", str(_exc)[:100])
                    # 每小时清一次已触发标记，避免无限增长
                    if len(_fired) > 500:
                        _fired.clear()
                except Exception as _aexc:  # noqa: BLE001
                    logger.warning("[higan] 定点查看异常（继续）：%r", str(_aexc)[:100])

                if not poll:
                    continue'''


def main() -> int:
    """执行。"""
    src = open(MAIN, encoding="utf-8").read()
    if "wake-at" in src:
        print("  - 已加过定点查看")
        return 0
    if OLD not in src:
        print("  ✗ 锚点不匹配")
        return 1
    bak = MAIN + ".bak-wakeat-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(MAIN, bak)
    src = src.replace(OLD, NEW, 1)
    with open(MAIN, "w", encoding="utf-8") as handle:
        handle.write(src)
    res = subprocess.run([sys.executable, "-m", "py_compile", MAIN],
                         capture_output=True, text=True)
    if res.returncode != 0:
        shutil.copy2(bak, MAIN)
        print("  ✗ 编译失败已还原：", res.stderr[-400:])
        return 1
    print("  ✓ 定点查看已加（备份 %s）" % os.path.basename(bak))

    # 初始化配置 + 移除之前的 hours（语义纠正）
    import json
    p = "/root/higan/groups.json"
    try:
        conf = json.load(open(p, encoding="utf-8"))
    except Exception:                                      # noqa: BLE001
        conf = {}
    conf.setdefault("wake_at", {})
    for _qq, _item in (conf.get("wake_person_conf") or {}).items():
        if isinstance(_item, dict) and "hours" in _item:
            _item.pop("hours", None)
    with open(p, "w", encoding="utf-8") as handle:
        json.dump(conf, handle, ensure_ascii=False, indent=2)
    print("  ✓ groups.json：新增 wake_at，并移除旧的 hours（语义纠正）")

    subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
    time.sleep(20)
    st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                        capture_output=True, text=True).stdout.strip()
    print("  astrbot-186:", st or "unknown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
