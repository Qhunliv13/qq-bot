# -*- coding: utf-8 -*-
"""项目自检：**文档 / 配置 schema / 代码 三者的一致性**。

检查项：
  ① README 与 CONFIGURATION 表格里点名的常量，是否**真的存在于 main.py**
     （名字写错会让人去改一个不存在的常量）；
  ② `_conf_schema.json` 的键，是否都被 `config.Settings` 声明且被 `from_mapping` 读取
     （schema 有、代码不读 → 用户配了不生效）；
  ③ `Settings` 的字段是否都在 schema 里（漏了 → 面板里配不了）；
  ④ 示例配置的键是否都在 schema 里（多写会误导）。

用法：
    python tools/check_docs_consistency.py
退出码：0 = 一致；1 = 存在不一致。
"""
from __future__ import annotations

import ast
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
PROJ = HERE.parent                      # 项目根（tools/ 的上一级）
PLUGIN = PROJ / "astrbot_plugin_group_ai"


def main_constants() -> set[str]:
    """main.py 的模块级常量名。"""
    tree = ast.parse((PLUGIN / "main.py").read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def settings_fields() -> set[str]:
    """config.Settings 的字段名。"""
    tree = ast.parse((PLUGIN / "config.py").read_text(encoding="utf-8"))
    fields = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    fields.add(item.target.id)
    return fields


def mapping_keys() -> set[str]:
    """from_mapping 里实际读取的配置键。"""
    src = (PLUGIN / "config.py").read_text(encoding="utf-8")
    return set(re.findall(r'data\.get\(\s*"([a-z_]+)"', src))


def doc_constants(path: pathlib.Path) -> set[str]:
    """文档表格里以反引号点名的全大写常量。

    排除一些**不是常量**的大写词（它们是模型输出标记或通用缩写），
    否则会产生噪音：`SILENT` 是模型表示"不接话"的输出，不是 Python 常量。
    """
    text = path.read_text(encoding="utf-8")
    found = set(re.findall(r"`([A-Z][A-Z0-9_]{3,})`", text))
    return found - {"SILENT", "HTTP", "HTTPS", "URL", "IP", "QQ", "MB", "API",
                    "JSON", "AST", "UTF"}


def main() -> int:
    """跑一致性检查。"""
    ok = True
    consts = main_constants()
    fields = settings_fields()
    mkeys = mapping_keys()
    schema = json.loads((PLUGIN / "_conf_schema.json").read_text(encoding="utf-8"))
    example = json.loads((PROJ / "examples/config.example.json").read_text(encoding="utf-8"))

    print("=== ① 文档点名的常量是否存在 ===")
    for doc in (PROJ / "README.md", PROJ / "docs/CONFIGURATION.md"):
        named = doc_constants(doc)
        missing = sorted(n for n in named if n not in consts
                         and n not in {"SK", "HTTP", "URL", "IP"})
        print("  %s：点名 %d 个；不存在 → %s"
              % (doc.name, len(named), missing or "（无）"))
        if missing:
            ok = False

    print("\n=== ② schema 键 vs Settings 字段 vs from_mapping 读取 ===")
    sk = set(schema) - {"_comment"}
    only_schema = sorted(sk - fields)
    only_fields = sorted(fields - sk)
    not_read = sorted(sk - mkeys)
    print("  schema 键（%d）：%s" % (len(sk), sorted(sk)))
    print("  Settings 字段（%d）：%s" % (len(fields), sorted(fields)))
    print("  只在 schema、Settings 没有：%s" % (only_schema or "（无）"))
    print("  只在 Settings、schema 没有（面板里配不了）：%s" % (only_fields or "（无）"))
    print("  schema 有但 from_mapping 没读（配了不生效）：%s" % (not_read or "（无）"))
    if only_schema or only_fields or not_read:
        ok = False

    print("\n=== ③ 示例配置 vs schema ===")
    ek = set(example) - {"_comment"}
    extra = sorted(ek - sk)
    lack = sorted(sk - ek)
    print("  示例多出（会误导）：%s" % (extra or "（无）"))
    print("  示例未覆盖（可接受）：%s" % (lack or "（无）"))
    if extra:
        ok = False

    print("\n结论：%s" % ("✓ 三者一致" if ok else "✗ 存在不一致"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
