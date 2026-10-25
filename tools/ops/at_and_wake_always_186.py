#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""@ 与唤醒人 ⇒ 都永远回（由模型自己决定沉默）+ 修 _wake_hit 适配按群存储（2026-10-25）。

用户要求：
  · @ 她 ⇒ 一定进回复判定（除非模型自己选择沉默）
  · 唤醒人说话 ⇒ 同上
  · 唤醒词同理

问题：wake_persons/wake_words 已改成按群 dict ✗ 而 _wake_hit 还按 list 读 ⇒ 失效
修法：_wake_hit(event, text, group) 支持 dict（取本群列表），并在门禁里带上 group
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time

MAIN = "/root/astrbot-src/data/plugins/astrbot_plugin_higan/main.py"

OLD_FN = '''    def _wake_hit(self, event: AstrMessageEvent, text: str) -> bool:
        """命中她设的唤醒词/唤醒人 ⇒ 当作被叫到（绕过回复率门禁）。

        Args:
            event: 当前消息事件。
            text: 消息文本。

        Returns:
            bool: 是否命中。
        """
        try:
            with open("/root/higan/groups.json", encoding="utf-8") as handle:
                conf = json.load(handle)
        except Exception:                                  # noqa: BLE001
            return False
        for word in (conf.get("wake_words") or []):
            w = str(word).strip()
            if w and w in (text or ""):
                return True
        try:
            sender = str(event.get_sender_id() or "")
        except Exception:                                  # noqa: BLE001
            sender = ""
        return bool(sender) and sender in [str(q) for q in (conf.get("wake_persons") or [])]
'''

NEW_FN = '''    def _wake_hit(self, event: AstrMessageEvent, text: str,
                  group: str = "") -> str:
        """命中唤醒词/唤醒人 ⇒ 返回命中原因（"person"/"word"/""）。

        支持按群存储（wake_persons / wake_words 都是 {群号: [..]}）。

        Args:
            event: 当前消息事件。
            text: 消息文本。
            group: 当前群号。

        Returns:
            str: "person" / "word" / ""（未命中）。
        """
        try:
            with open("/root/higan/groups.json", encoding="utf-8") as handle:
                conf = json.load(handle)
        except Exception:                                  # noqa: BLE001
            return ""

        def _list_for(key: str) -> list:
            """取本群的名单（兼容旧的 list 写法）。"""
            val = conf.get(key) or {}
            if isinstance(val, dict):
                return [str(x) for x in (val.get(str(group)) or [])]
            return [str(x) for x in val]

        try:
            sender = str(event.get_sender_id() or "")
        except Exception:                                  # noqa: BLE001
            sender = ""
        if sender and sender in _list_for("wake_persons"):
            return "person"
        for word in _list_for("wake_words"):
            w = word.strip()
            if w and w in (text or ""):
                return "word"
        return ""
'''

OLD_CALL = """                _woken_e = (self._is_woken(event) or self._wake_hit(event, text)
                            or _at_me_now)"""

NEW_CALL = """                _wake_why = self._wake_hit(event, text, str(group))
                _woken_e = bool(self._is_woken(event)) or _at_me_now or bool(_wake_why)
                if _wake_why:
                    logger.info("[higan] 命中唤醒%s ⇒ 直接进入回复判定"
                                % ("人" if _wake_why == "person" else "词"))"""


def main() -> int:
    """执行。"""
    src = open(MAIN, encoding="utf-8").read()
    changed = False
    bak = MAIN + ".bak-wakehit-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(MAIN, bak)

    if OLD_FN in src:
        src = src.replace(OLD_FN, NEW_FN, 1)
        changed = True
        print("  ✓ _wake_hit 支持按群 + 返回命中原因")
    elif "def _wake_hit(self, event: AstrMessageEvent, text: str,\n                  group: str = \"\") -> str:" in src:
        print("  - _wake_hit 已是新版")
    else:
        print("  ✗ 找不到 _wake_hit（旧版或已被改）")

    if OLD_CALL in src:
        src = src.replace(OLD_CALL, NEW_CALL, 1)
        changed = True
        print("  ✓ 门禁改用 命中原因 判定（@ / 唤醒人 / 唤醒词 都绕过概率）")
    else:
        print("  - 门禁调用未找到旧写法")

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
