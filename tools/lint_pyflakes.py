# -*- coding: utf-8 -*-
"""pyflakes 检查器（绕开 `python -m pyflakes`）。

**为什么要这个脚本**：本机 `python -m pyflakes <文件>` 会**挂住不返回**
（实测连 `--version` 都卡；而 `import pyflakes` 本身只要几十毫秒）——
大概率是该入口在等 stdin。故直接用 pyflakes 的 API 检查，稳定且能拿到计数。

用法：
    python tools/lint_pyflakes.py <文件或目录> [...]
退出码：0 = 无问题；1 = 有问题（或文件读不到）。
"""
from __future__ import annotations

import pathlib
import sys

from pyflakes.api import checkPath
from pyflakes.reporter import Reporter


def collect(targets: list[str]) -> list[str]:
    """把参数展开成待检查的 .py 文件列表（目录则递归取其下所有 .py）。"""
    files: list[str] = []
    for t in targets:
        p = pathlib.Path(t)
        if p.is_dir():
            files.extend(str(x) for x in sorted(p.rglob("*.py")))
        elif p.is_file():
            files.append(str(p))
        else:
            print("路径不存在：%s" % t, file=sys.stderr)
    return files


def main(argv: list[str]) -> int:
    """逐个文件检查，汇总告警数。"""
    if not argv:
        print("用法：python tools/lint_pyflakes.py <文件或目录> [...]", file=sys.stderr)
        return 1
    files = collect(argv)
    if not files:
        print("没有可检查的 .py 文件", file=sys.stderr)
        return 1
    reporter = Reporter(sys.stdout, sys.stderr)
    total = 0
    for f in files:
        total += checkPath(f, reporter)
    print("检查 %d 个文件，共 %d 条告警" % (len(files), total))
    return 0 if total == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
