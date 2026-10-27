#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出 wake_v2_186.py 里的 TOOLS 文本块到单独文件（给第二版补丁用）。"""

from __future__ import annotations

import re
import sys

SRC = r"C:\qhunliv13_web\tools\ops\wake_v2_186.py"
DST = r"C:\qhunliv13_web\local-her\wake_tools_block.py"


def main() -> int:
    """取三引号里的 TOOLS 内容并落盘。"""
    text = open(SRC, encoding="utf-8").read()
    match = re.search(r"^TOOLS = '''(.*?)'''", text, re.S | re.M)
    if not match:
        print("  ✗ 没找到 TOOLS")
        return 1
    body = match.group(1)
    with open(DST, "w", encoding="utf-8") as handle:
        handle.write(body)
    print("  ✓ 已导出 %d 字节 → %s" % (len(body), DST))
    return 0


if __name__ == "__main__":
    sys.exit(main())
