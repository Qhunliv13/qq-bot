#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打印精简后的人格 + 事实抽样（2026-10-25）。"""

from __future__ import annotations

import json
import sqlite3
import sys

DB = "/root/astrbot-src/data/data_v4.db"
MEM = "/root/higan/memory/memory.json"


def main() -> int:
    """输出。"""
    con = sqlite3.connect(DB)
    row = con.execute("select system_prompt from personas where persona_id='higan'").fetchone()
    con.close()
    persona = (row[0] if row else "") or ""
    print("=== 人格（精简后 %d 字）===" % len(persona))
    print(persona)

    data = json.load(open(MEM, encoding="utf-8"))
    facts = (data.get("global") or {}).get("facts") or []
    print("=== 事实（共 %d 条）前 8 条 ===" % len(facts))
    for item in facts[:8]:
        print("  -", str(item.get("text"))[:80])
    return 0


if __name__ == "__main__":
    sys.exit(main())
