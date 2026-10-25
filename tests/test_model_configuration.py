"""Validate explicit provider overrides without importing the host framework."""
import ast
import json
import logging
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "plugin" / "main.py"


def instance(root):
    """Extract the actual configuration adapter rather than replacing it in tests."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "HiganPlugin")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_llm_extra")
    ns = {"Path": Path, "HIGAN_ROOT": str(root), "json": json, "logger": logging.getLogger(__name__)}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(SOURCE), "exec"), ns)
    return type("ActualConfig", (), {"_llm_extra": ns["_llm_extra"]})()


def test_missing_config_adds_no_token_or_reasoning_default(tmp_path):
    """Provider chooses its own defaults when deployment has specified none."""
    assert instance(tmp_path)._llm_extra() == {}


def test_explicit_provider_settings_are_preserved(tmp_path):
    """User configuration, not an arbitrary plugin clamp, selects provider parameters."""
    (tmp_path / "llm.json").write_text(json.dumps({"max_tokens": 19000, "reasoning_effort": "medium"}), encoding="utf-8")
    assert instance(tmp_path)._llm_extra() == {"max_tokens": 19000, "reasoning_effort": "medium"}


def test_invalid_override_does_not_invent_a_new_limit(tmp_path):
    """Malformed optional config is logged and does not silently install a4096 cap."""
    (tmp_path / "llm.json").write_text("{invalid", encoding="utf-8")
    assert instance(tmp_path)._llm_extra() == {}
