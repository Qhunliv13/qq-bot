#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提示词尾部只保留事实 + 过滤 DSML/XML 工具调用泄漏（2026-10-25）。

用户：
  · 把【当前状态】【关于这些人/这件事的记忆】【相关背景】【你最近说过的】【现在】…全删
  · 后面只需要追加事实
  · 另外她刚才把工具调用语法 `<||DSML|| calls>` 当正文发到群里了，必须拦掉
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time

BASE = "/root/astrbot-src/data/plugins/astrbot_plugin_higan"
MAIN = os.path.join(BASE, "main.py")
PROMPTS = os.path.join(BASE, "prompts_v2.py")

OLD_TAIL = '''    # 尾部（可变）：状态 / 记忆 / 世界书 / 行动提示
    tail: list[str] = []
    state = ["你本分钟已经说了 %d 次" % said_this_minute]
    if seconds_since is not None:
        state.append("距你上次发言 %d 秒" % seconds_since)
    tail.append("【当前状态】" + " ｜ ".join(state))
    if summary:
        tail.append("【关于这些人/这件事的记忆】" + str(summary))
    facts = [str(f) for f in (facts or []) if str(f).strip()]
    if facts:
        # 只放在最后一条 user 消息尾部 ⇒ 前缀缓存不受影响 ✓
        # 截断到 80 字、最多 30 条，控制 token 增量 ✓
        tail.append("【你记得的事】" + "；".join(facts))
    hits = world_book_hits(latest + "\\n" + "\\n".join(lines))
    if hits:
        tail.append("【相关背景】\\n" + "\\n".join("- " + h for h in hits))
    mine = [str(x).strip() for x in (my_recent or []) if str(x).strip()][-3:]
    if mine:
        tail.append("【你最近说过的（别重复 ✓）】\\n" + "\\n".join("- " + m[:120] for m in mine))
    tail.append("【现在】决定这条要不要接：要接就按 JSON 说，不接就给 action=silent。")
'''

NEW_TAIL = '''    # [facts-only] 2026-10-25 用户要求：尾部只追加事实，其余状态块全部去掉
    tail: list[str] = []
    facts = [str(f) for f in (facts or []) if str(f).strip()]
    if facts:
        # 只放在最后一条 user 消息尾部 ⇒ 前缀（system+历史）不变，缓存照旧命中
        tail.append("【你记得的事】" + "；".join(facts))
'''

LEAK_PATTERNS = [
    r"<\|+\s*\|?DSML[^>]*>.*?</\|+\s*\|?DSML[^>]*>",
    r"<[^>]*DSML[^>]*>.*",
    r"<\|[^>]*\|>",
]


def compile_ok(path: str) -> bool:
    """语法检查。"""
    res = subprocess.run([sys.executable, "-m", "py_compile", path],
                         capture_output=True, text=True)
    if res.returncode != 0:
        print("  ✗ 编译失败", path, res.stderr[-250:])
    return res.returncode == 0


def main() -> int:
    """执行。"""
    changed = False

    src = open(PROMPTS, encoding="utf-8").read()
    if "[facts-only]" not in src:
        shutil.copy2(PROMPTS, PROMPTS + ".bak-factsonly-" + time.strftime("%Y%m%d-%H%M%S"))
        if OLD_TAIL not in src:
            print("  ✗ prompts_v2 找不到尾部块")
            return 1
        src = src.replace(OLD_TAIL, NEW_TAIL, 1)
        with open(PROMPTS, "w", encoding="utf-8") as handle:
            handle.write(src)
        if not compile_ok(PROMPTS):
            return 1
        print("  ✓ 提示词尾部只保留事实")
        changed = True

    msrc = open(MAIN, encoding="utf-8").read()
    if "_strip_tool_syntax" not in msrc:
        helper = '''
    @staticmethod
    def _strip_tool_syntax(text: str) -> str:
        """把模型误输出的工具调用语法（DSML/XML 那类）清掉，别发到群里。

        Args:
            text: 待发文本。

        Returns:
            str: 清理后的文本（可能为空 ⇒ 调用方按沉默处理）。
        """
        out = str(text or "")
        for pat in (r"<\\|+\\s*\\|?DSML[\\s\\S]*", r"<\\|[\\s\\S]*?\\|>",
                    r"<\\s*/?\\s*invoke[\\s\\S]*", r"<\\s*/?\\s*calls[\\s\\S]*"):
            out = re.sub(pat, "", out)
        return out.strip()

'''
        anchor = "    async def _call_llm(self, provider, prompt: str, extra_system: str,"
        msrc = msrc.replace(anchor, helper + anchor, 1)

        # 在最终返回回复处统一过滤
        msrc = msrc.replace(
            '        logger.info("[higan] 工具优先通路产出回复（%d 字）" % len(_out))\n        return _out[:1500]',
            '        _out = self._strip_tool_syntax(_out)\n'
            '        if not _out:\n            return ""\n'
            '        logger.info("[higan] 工具优先通路产出回复（%d 字）" % len(_out))\n        return _out[:1500]', 1)
        msrc = msrc.replace(
            '        _sent = await event.send(chain)',
            '        _clean = self._strip_tool_syntax(_text) if "_text" in dir() else ""\n'
            '        _sent = await event.send(chain)', 1)
        shutil.copy2(MAIN, MAIN + ".bak-leak-" + time.strftime("%Y%m%d-%H%M%S"))
        with open(MAIN, "w", encoding="utf-8") as handle:
            handle.write(msrc)
        if not compile_ok(MAIN):
            return 1
        print("  ✓ 增加 DSML/工具语法过滤")
        changed = True

    if changed:
        subprocess.run(["systemctl", "restart", "astrbot-186"], check=False)
        time.sleep(20)
        st = subprocess.run(["systemctl", "is-active", "astrbot-186"],
                            capture_output=True, text=True).stdout.strip()
        print("  astrbot-186:", st or "unknown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
