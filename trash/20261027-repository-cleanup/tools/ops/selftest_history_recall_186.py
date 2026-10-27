#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自测：历史检索能否找回"酒狐/瑞禾"的旧对话（2026-10-25）。"""

from __future__ import annotations

import glob
import json
import sys


def main() -> int:
    """跑一次检索。"""
    for name in ("酒狐", "瑞禾"):
        hits = []
        for path in sorted(glob.glob("/root/higan/history/*.jsonl")):
            with open(path, encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if name in line:
                        try:
                            rec = json.loads(line)
                        except Exception:                 # noqa: BLE001
                            continue
                        hits.append(str(rec.get("line") or "")[:100])
        print("  %s：命中 %d 条" % (name, len(hits)))
        for item in hits[-2:]:
            print("    ·", item)
    return 0


if __name__ == "__main__":
    sys.exit(main())
