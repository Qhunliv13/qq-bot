"""Offline AST probe of actual deployed mention admission; never posts or invokes model/tools."""
import argparse
import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import types
import sys


class At:
    """Minimal incoming mention component for no-platform probe."""

    def __init__(self, qq):
        self.qq = qq


class Event:
    """No send method: successful test cannot accidentally deliver a QQ message."""

    def __init__(self, components, woken=False):
        self.components = components
        self.message_obj = SimpleNamespace(message=components)
        self.woken = woken

    def get_self_id(self):
        """Use a synthetic bot identity, unrelated to real accounts."""
        return "90001"

    def get_messages(self):
        """Expose only actual top-level components."""
        return self.components


async def probe(plugin, config, group):
    """Execute extracted deployed admission methods against actual saved policy without mutation."""
    package = types.ModuleType("policy_probe_plugin")
    package.__path__ = [str(plugin)]
    sys.modules[package.__name__] = package
    tree = ast.parse((plugin / "main.py").read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "HiganPlugin")
    methods = {"_at_targets", "_directly_mentioned", "_policy_allows", "_group_policy"}
    cls.body = [node for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in methods]
    cls.decorator_list = []
    cls.bases = []
    ns = {"__name__": package.__name__ + ".main", "__package__": package.__name__, "At": At,
          "AstrMessageEvent": object, "HIGAN_ROOT": str(config.parent), "Path": Path,
          "logger": SimpleNamespace(warning=lambda *args: None)}
    selected = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), cls], type_ignores=[])
    exec(compile(ast.fix_missing_locations(selected), str(plugin / "main.py"), "exec"), ns)
    bot = ns["HiganPlugin"]()
    actual = bot._group_policy(group)
    result = {
        "saved_mention_only": group in actual["mention_only"],
        "plain_denied": not bot._policy_allows(Event([]), group),
        "quote_wake_without_at_denied": not bot._policy_allows(Event([], woken=True), group),
        "at_other_denied": not bot._policy_allows(Event([At("90002")]), group),
        "actual_self_at_allowed": bot._policy_allows(Event([At("90001")]), group),
        "raw_segment_self_at_allowed": bot._policy_allows(Event([{"type": "at", "data": {"qq": "90001"}}]), group),
        "private_preserved": bot._policy_allows(Event([]), "private:12345"),
        "qq_messages_sent": 0,
    }
    print(json.dumps(result))
    return 0 if all(value for key, value in result.items() if key != "qq_messages_sent") else 1


def main():
    """Require exact deployed plugin/config/scope; no production source/config changes."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--group", required=True)
    args = parser.parse_args()
    return asyncio.run(probe(args.plugin, args.config, args.group))


if __name__ == "__main__":
    raise SystemExit(main())
