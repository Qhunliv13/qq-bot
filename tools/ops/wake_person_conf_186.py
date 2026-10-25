#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B：给单个唤醒人单独配置「回复率 / 专属唤醒词 / 唤醒时间段」（2026-10-25）。

配置（groups.json，不写死在代码里）：
  wake_person_conf: {
    "3643349392": {"pct": 100, "words": ["瑞禾"], "hours": [8, 24]}
  }
语义：
  · hours 命中（支持跨零点）且 pct 抽中 ⇒ 视为被叫到（绕过群概率）
  · 他的消息里出现他的专属唤醒词 ⇒ 无视 hours/pct，直接进判定
  · hours 之外 ⇒ 只按他的 pct 抽（默认 100 ⇒ 仍然回）
工具：wake_person_set(qq, pct?, words?, hours?)、wake_person_show(qq)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

MAIN = "/root/astrbot-src/data/plugins/astrbot_plugin_higan/main.py"

HELPER = '''    def _person_conf(self, qq: str) -> dict:
        """取某个唤醒人的专属配置（回复率/专属词/时间段）。

        Args:
            qq: 对方 QQ 号。

        Returns:
            dict: {"pct": float, "words": list, "hours": list}（缺省为全开）。
        """
        try:
            with open("/root/higan/groups.json", encoding="utf-8") as handle:
                conf = json.load(handle)
        except Exception:                                  # noqa: BLE001
            return {"pct": 100.0, "words": [], "hours": []}
        table = conf.get("wake_person_conf") or {}
        item = table.get(str(qq)) or table.get(str(qq).strip()) or {}
        try:
            pct = float(item.get("pct", 100))
        except Exception:                                  # noqa: BLE001
            pct = 100.0
        return {"pct": max(0.0, min(100.0, pct)),
                "words": [str(x) for x in (item.get("words") or [])],
                "hours": [int(x) for x in (item.get("hours") or [])][:2]}

    @staticmethod
    def _hours_ok(hours: list) -> bool:
        """当前时间是否落在配置的小时区间内（支持跨零点；空列表=不限）。"""
        if not hours or len(hours) < 2:
            return True
        start, end = hours[0], hours[1]
        now = time.localtime().tm_hour
        if start <= end:
            return start <= now < end
        return now >= start or now < end

'''

SCHEMA_ANCHOR = '            _f("wake_list",'
SCHEMA_ADD = '''            _f("wake_person_set", "给某个唤醒人单独设回复率/专属唤醒词/唤醒时间段",
               {"qq": {"type": "string"}, "pct": {"type": "number"},
                "words": {"type": "array"}, "hours": {"type": "array"}}, ["qq"]),
            _f("wake_person_show", "看某个唤醒人的专属配置", {"qq": {"type": "string"}}, ["qq"]),
'''

DISPATCH_ANCHOR = '        if name in ("wake_list", "wake_add", "wake_remove", "wake_search"):'
DISPATCH_ADD = '''        if name in ("wake_person_set", "wake_person_show"):
            # [wake-person-conf] 单唤醒人配置：pct / words / hours
            try:
                _p = "/root/higan/groups.json"
                with open(_p, encoding="utf-8") as _h:
                    _c = json.load(_h)
                _tbl = _c.setdefault("wake_person_conf", {})
                _qq = str(args.get("qq") or "").strip()
                if not _qq:
                    return {"ok": False, "error": "要给 qq"}
                if name == "wake_person_show":
                    return {"ok": True, "qq": _qq, "conf": _tbl.get(_qq) or {}}
                _item = _tbl.setdefault(_qq, {})
                if args.get("pct") is not None:
                    _item["pct"] = max(0.0, min(100.0, float(args["pct"])))
                if isinstance(args.get("words"), list):
                    _item["words"] = [str(x) for x in args["words"]][:20]
                if isinstance(args.get("hours"), list):
                    _item["hours"] = [int(x) for x in args["hours"]][:2]
                with open(_p, "w", encoding="utf-8") as _h:
                    json.dump(_c, _h, ensure_ascii=False, indent=2)
                return {"ok": True, "qq": _qq, "conf": _item}
            except Exception as _exc:  # noqa: BLE001
                return {"ok": False, "error": str(_exc)[:150]}

'''

GATE_OLD = '''                _wake_why = self._wake_hit(event, text, str(group))
                _woken_e = bool(self._is_woken(event)) or _at_me_now or bool(_wake_why)
                if _wake_why:
                    logger.info("[higan] 命中唤醒%s ⇒ 直接进入回复判定"
                                % ("人" if _wake_why == "person" else "词"))'''

GATE_NEW = '''                _wake_why = self._wake_hit(event, text, str(group))
                _woken_e = bool(self._is_woken(event)) or _at_me_now or bool(_wake_why)
                if _wake_why:
                    logger.info("[higan] 命中唤醒%s ⇒ 直接进入回复判定"
                                % ("人" if _wake_why == "person" else "词"))
                # [wake-person-conf] 唤醒人可单独配 pct / 专属词 / 时间段
                if _wake_why == "person":
                    try:
                        _sender = str(event.get_sender_id() or "")
                        _pc = self._person_conf(_sender)
                        _hit_word = any(w and w in (text or "") for w in _pc["words"])
                        if _hit_word:
                            logger.info("[higan] 命中他的专属唤醒词 ⇒ 必定进判定")
                            _woken_e = True
                        elif self._hours_ok(_pc["hours"]) and random.random() * 100 < _pc["pct"]:
                            logger.info("[higan] 唤醒人 %s 命中（pct=%s，时段内）⇒ 进判定"
                                        % (_sender, _pc["pct"]))
                            _woken_e = True
                        else:
                            logger.info("[higan] 唤醒人 %s 本次不触发（pct=%s，时段=%s）"
                                        % (_sender, _pc["pct"], _pc["hours"] or "不限"))
                            _woken_e = False
                    except Exception as _pexc:  # noqa: BLE001
                        logger.warning("[higan] 唤醒人配置读取失败：%r", str(_pexc)[:100])'''


def main() -> int:
    """执行。"""
    src = open(MAIN, encoding="utf-8").read()
    changed = False
    bak = MAIN + ".bak-wpconf-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(MAIN, bak)

    if "_person_conf" not in src:
        anchor = "    def _wake_hit(self, event: AstrMessageEvent, text: str,"
        if anchor not in src:
            print("  ✗ 找不到 _wake_hit")
            return 1
        src = src.replace(anchor, HELPER + anchor, 1)
        changed = True
        print("  ✓ 加 _person_conf / _hours_ok")

    if '"wake_person_set"' not in src:
        pos = src.index(SCHEMA_ANCHOR)
        start = src.rindex("\n", 0, pos) + 1
        src = src[:start] + SCHEMA_ADD + src[start:]
        pos = src.index(DISPATCH_ANCHOR)
        start = src.rindex("\n", 0, pos) + 1
        src = src[:start] + DISPATCH_ADD + src[start:]
        changed = True
        print("  ✓ 加工具 wake_person_set / wake_person_show")

    if GATE_OLD in src:
        src = src.replace(GATE_OLD, GATE_NEW, 1)
        changed = True
        print("  ✓ 门禁接入单唤醒人配置")
    else:
        print("  - 门禁锚点未匹配（可能已改）")

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

    # 初始化配置表（幂等）
    import json
    try:
        conf = json.load(open("/root/higan/groups.json", encoding="utf-8"))
    except Exception:                                      # noqa: BLE001
        conf = {}
    conf.setdefault("wake_person_conf", {})
    with open("/root/higan/groups.json", "w", encoding="utf-8") as handle:
        json.dump(conf, handle, ensure_ascii=False, indent=2)
    print("  ✓ groups.json 已有 wake_person_conf（当前 %d 个）"
          % len(conf["wake_person_conf"]))

    subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
    time.sleep(20)
    st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                        capture_output=True, text=True).stdout.strip()
    print("  astrbot-186:", st or "unknown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
