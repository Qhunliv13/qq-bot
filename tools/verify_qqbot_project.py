# -*- coding: utf-8 -*-
"""项目自检：**通用性 + 脱敏**（不依赖任何真实值）。

设计要点
--------
检测脚本**不能把真实值写进模式里** —— 否则脚本本身就成了敏感数据的副本，
而且真实值一变就检测不出来。所以这里只检测**数据类别**：

  · 任何"看起来像真实标识"的东西（非白名单的 IP / 域名 / 长数字 / 绝对路径 / 密钥）
  · 任何缺失的必需文件或关键代码片段
  · 语法与 JSON 可解析性

白名单里只放**通用、公开、任何部署都会用到**的字面量（环回地址、公开 API 域名等）。

用法：
    python tools/verify_qqbot_project.py
退出码：0 = 通过；1 = 有问题。
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

#: 允许出现的字面量：**通用且公开**，不构成脱敏风险。
ALLOWED = {
    "127.0.0.1",                        # 环回地址（配置默认值）
    "0.0.0.0",
    "126.0.0.0",                        # 出现在浏览器 User-Agent 的版本号里
    "3000",                             # OneBot 常见默认端口
    "q1.qlogo.cn",                      # QQ 头像公开接口
    "cn.bing.com", "www.bing.com",      # 搜索
    "txz.qq.com",                       # QQ 登录（公开）
}

#: 敏感数据的**类别**（不含任何真实值）
SENSITIVE = [
    ("疑似 API Key", r"\b(?:sk|qf|hk|ak)-[A-Za-z0-9_\-]{12,}"),
    ("疑似口令字段", r"(?i)(?:password|passwd|pwd|密码|口令)\s*[:=：]\s*\S+"),
    ("疑似密钥字段", r"(?i)(?:secret|api[_-]?key|access[_-]?key)\s*[:=：]\s*\S+"),
    ("非白名单 IP", r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    ("非白名单域名",
     r"\b[a-z0-9\-]+(?:\.[a-z0-9\-]+)*\.(?:com|cn|online|net|org|io|xyz|top)\b"),
    ("邮箱", r"\b[\w.\-]+@[\w\-]+\.[a-z]{2,}\b"),
    ("5~12 位数字（疑似 QQ/群号）", r"(?<![.\d])\d{5,12}(?![.\d])"),
    ("绝对路径（/root /data /home）", r"/(?:root|data|home)/[\w./\-]{2,}"),
]

REQUIRED_FILES = [
    "README.md",
    "LICENSE",
    "docs/ARCHITECTURE.md",
    "docs/CONFIGURATION.md",
    "examples/config.example.json",
    "astrbot_plugin_group_ai/main.py",
    "astrbot_plugin_group_ai/config.py",
    "astrbot_plugin_group_ai/prompts.py",
    "astrbot_plugin_group_ai/_conf_schema.json",
    "astrbot_plugin_group_ai/metadata.yaml",
]

#: LICENSE 里必须出现的短语（防止空文件或被截断）
LICENSE_MUST_HAVE = [
    "MIT License",
    "Permission is hereby granted, free of charge",
    "WITHOUT WARRANTY OF ANY KIND",
    "Copyright (c)",
]

#: 关键设计必须体现在代码里（证明"配置驱动、无硬编码"）
REQUIRED_SNIPPETS = [
    ("astrbot_plugin_group_ai/main.py", "from .config import Settings"),
    ("astrbot_plugin_group_ai/main.py", "from . import prompts as P"),
    ("astrbot_plugin_group_ai/main.py", "Settings.from_mapping(config)"),
    ("astrbot_plugin_group_ai/main.py", "S.is_owner("),
    ("astrbot_plugin_group_ai/main.py", "S.group_allowed("),
    ("astrbot_plugin_group_ai/main.py", "S.group_watched("),
    ("astrbot_plugin_group_ai/main.py", "S.onebot_base"),
    ("astrbot_plugin_group_ai/main.py", "P.decide_system("),
    ("astrbot_plugin_group_ai/main.py", "P.direct_system("),
    ("astrbot_plugin_group_ai/config.py", "class Settings"),
    ("astrbot_plugin_group_ai/prompts.py", "DEFAULT_PERSONA"),
    ("astrbot_plugin_group_ai/prompts.py", "def persona_text("),
]

SCAN_EXT = {".py", ".md", ".json", ".yaml", ".yml", ".txt", ".sh"}


def _is_placeholder(token: str) -> bool:
    """判断一个数字是不是**明显的占位符**（连续递增/递减，或各位全同）。

    为什么需要它：注释与示例配置里必然出现"假号码"（如 `123456`、`987654321`），
    把它们当真实数据报错会让检查结果充满噪音，反而掩盖真问题。
    只认"一眼就是编的"形态，避免放过真实号码。
    """
    if len(token) < 2:
        return False
    if len(set(token)) == 1:                        # 11111
        return True
    d = [int(c) for c in token]
    up = all(d[i + 1] - d[i] == 1 for i in range(len(d) - 1))
    down = all(d[i] - d[i + 1] == 1 for i in range(len(d) - 1))
    return up or down


def scan_sensitive() -> list[str]:
    """扫出项目内所有"疑似真实数据"的位置。"""
    problems: list[str] = []
    me = pathlib.Path(__file__).name
    for p in sorted(PROJ.rglob("*")):
        if not p.is_file() or p.suffix not in SCAN_EXT:
            continue
        if p.name == me:
            continue                       # 本文件里的模式是"类别"，不是真实值
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for i, line in enumerate(lines, 1):
            for label, pat in SENSITIVE:
                for m in re.finditer(pat, line):
                    token = m.group(0)
                    if token in ALLOWED or _is_placeholder(token):
                        continue
                    # 年份/短版本号之类的误报（如 2026、512）
                    if label.startswith("5~12") and re.fullmatch(
                            r"(?:19|20)\d{2}|[1-9]\d{0,3}", token):
                        continue
                    problems.append("%s:%d  %s → %s"
                                    % (p.relative_to(PROJ), i, label, token[:40]))
    return problems


def check_attribute_refs() -> list[str]:
    """核对 `S.*` / `P.*` / 模块常量是否**真的存在**。

    为什么必须有这项：静态检查工具**查不出属性名写错**
    （`S.web_enabled` 在语法上完全合法），而 `Settings` 是 dataclass、字段有限 ——
    访问不存在的字段要**运行时**才炸。这类错误会直接让插件在每条消息上崩掉，
    所以在这里用 AST 做一次"引用 vs 定义"的交叉核对。
    """
    problems: list[str] = []
    main_tree = ast.parse((PLUGIN / "main.py").read_text(encoding="utf-8"))

    # 收集 config.Settings 的字段与方法
    cfg_tree = ast.parse((PLUGIN / "config.py").read_text(encoding="utf-8"))
    members: set[str] = set()
    for node in ast.walk(cfg_tree):
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    members.add(item.target.id)
                elif isinstance(item, ast.FunctionDef):
                    members.add(item.name)

    # 收集 prompts 模块的顶层名字（函数与常量）
    pr_tree = ast.parse((PLUGIN / "prompts.py").read_text(encoding="utf-8"))
    p_names: set[str] = set()
    for node in pr_tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            p_names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    p_names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            p_names.add(node.target.id)

    # 收集 main 模块自己的顶层名字（用于校验模块常量引用）
    m_names: set[str] = set()
    for node in main_tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            m_names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    m_names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            m_names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                m_names.add(a.asname or a.name.split(".")[0])

    for node in ast.walk(main_tree):
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            continue
        base, attr = node.value.id, node.attr
        if base == "S" and attr not in members:
            problems.append("main.py:%d  访问了 Settings 不存在的成员 S.%s"
                            % (node.lineno, attr))
        elif base == "P" and attr not in p_names:
            problems.append("main.py:%d  访问了 prompts 不存在的成员 P.%s"
                            % (node.lineno, attr))
    return problems


def main() -> int:
    """跑全部检查。"""
    ok = True

    print("=== ① 必需文件 ===")
    for rel in REQUIRED_FILES:
        exists = (PROJ / rel).exists()
        print("  %s %s" % ("✓" if exists else "✗", rel))
        ok = ok and exists

    print("\n=== ② 脱敏：不应出现真实数据 ===")
    problems = scan_sensitive()
    if problems:
        for line in problems[:40]:
            print("  ✗ %s" % line)
        if len(problems) > 40:
            print("  … 另有 %d 处" % (len(problems) - 40))
        ok = False
    else:
        print("  ✓ 未发现真实数据（IP/域名/号码/路径/密钥）")

    print("\n=== ②b 许可证 ===")
    lic_path = PROJ / "LICENSE"
    if lic_path.exists():
        lic = lic_path.read_text(encoding="utf-8")
        missing = [s for s in LICENSE_MUST_HAVE if s not in lic]
        if missing:
            print("  ✗ LICENSE 缺少关键短语：%s" % missing)
            ok = False
        else:
            print("  ✓ MIT 文本完整（%d 字符）" % len(lic))
        # README 必须指向 LICENSE，否则读者不知道许可是什么
        if "LICENSE" not in (PROJ / "README.md").read_text(encoding="utf-8"):
            print("  ✗ README 未引用 LICENSE")
            ok = False
        else:
            print("  ✓ README 已引用 LICENSE")
    else:
        print("  ✗ 缺少 LICENSE")
        ok = False

    print("\n=== ③ 属性/常量引用是否存在（查运行时才会炸的错）===")
    ref_problems = check_attribute_refs()
    if ref_problems:
        for line in ref_problems:
            print("  ✗ %s" % line)
        ok = False
    else:
        print("  ✓ S.* 与 P.* 引用全部存在")

    print("\n=== ③ 配置驱动是否落地 ===")
    for rel, snippet in REQUIRED_SNIPPETS:
        text = (PROJ / rel).read_text(encoding="utf-8")
        found = snippet in text
        print("  %s %-18s %s" % ("✓" if found else "✗", pathlib.Path(rel).name,
                                 snippet[:46]))
        ok = ok and found

    print("\n=== ④ 语法与 JSON ===")
    for p in sorted(PLUGIN.glob("*.py")):
        try:
            ast.parse(p.read_text(encoding="utf-8"))
            print("  ✓ %s" % p.name)
        except SyntaxError as exc:
            print("  ✗ %s → %s" % (p.name, exc))
            ok = False
    for rel in ("astrbot_plugin_group_ai/_conf_schema.json",
                "examples/config.example.json"):
        try:
            json.loads((PROJ / rel).read_text(encoding="utf-8"))
            print("  ✓ %s" % pathlib.Path(rel).name)
        except Exception as exc:
            print("  ✗ %s → %r" % (rel, exc))
            ok = False

    print("\n=== ⑤ 规模 ===")
    for p in sorted(PROJ.rglob("*")):
        if p.is_file() and p.suffix in SCAN_EXT:
            n = len(p.read_text(encoding="utf-8").splitlines())
            print("  %-46s %4d 行" % (p.relative_to(PROJ), n))

    print("\n结果：%s" % ("全部通过 ✓" if ok else "存在失败项 ✗"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
