"""Verify independent static inventory parsing and fail-closed interpretation."""

import importlib.util
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "tools" / "qq_native_binding_inventory.py"
SPEC = importlib.util.spec_from_file_location("qq_native_binding_inventory", SOURCE)
inventory = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inventory)


def test_symbol_definitions_do_not_confuse_undefined_imports():
    """A N-API import is dependency evidence, not a working exported initializer."""
    parsed = inventory.parse_symbols("""
    1: 0000000000000000 0 FUNC GLOBAL DEFAULT UND napi_create_function
    2: 0000000000000120 64 FUNC GLOBAL DEFAULT 12 qq_magic_napi_register
    3: 0000000000000200 64 FUNC LOCAL DEFAULT 12 hidden_kernel
    4: 0000000000000300 64 FUNC GLOBAL HIDDEN 12 hidden_session
    5: 0000000000000400 64 FUNC WEAK DEFAULT 12 node_module_register
    """)
    assert parsed["defined_dynamic_symbol_count"] == 2
    assert parsed["interesting_defined_symbols"] == ["node_module_register", "qq_magic_napi_register"]
    assert parsed["interesting_undefined_symbols"] == ["napi_create_function"]


def test_limits_exposed_names():
    """Hostile or huge symbol tables do not produce an unbounded public record."""
    text = "\n".join(f"{i}: 0000000000000001 1 FUNC GLOBAL DEFAULT 1 napi_{i}" for i in range(220))
    parsed = inventory.parse_symbols(text)
    assert parsed["interesting_symbols_truncated"] is True
    assert len(parsed["interesting_defined_symbols"]) == 200


def test_non_linux_refuses_without_native_execution(monkeypatch):
    """Static tooling never invokes or loads QQ as a Windows fallback."""
    monkeypatch.setattr(inventory.sys, "platform", "win32")
    report = inventory.inventory("C:/not-used")
    assert report["errors"] == ["linux_required"]
    for key in ("native_code_executed", "native_binding_verified", "safe_to_login", "independent_backend_ready", "production_cutover_authorized"):
        assert report[key] is False


def test_missing_candidate_refuses_before_tool_execution(tmp_path, monkeypatch):
    """Wrong package paths fail before a subprocess is spawned."""
    def forbidden(*args, **kwargs):
        """Reject any attempt to launch a binary for an absent candidate."""
        raise AssertionError("subprocess unexpectedly executed")
    monkeypatch.setattr(inventory.subprocess, "run", forbidden)
    with pytest.raises(ValueError, match="candidate_missing_or_symlink"):
        inventory.inspect_candidate(tmp_path, inventory.CANDIDATES[0])


def test_non_elf_rejected_before_tool(tmp_path):
    """A native suffix alone is not sufficient static format evidence."""
    target = tmp_path / "fake.node"
    target.write_bytes(b"x" * 64)
    with pytest.raises(ValueError, match="candidate_not_elf64_little_endian"):
        inventory.inspect_candidate(tmp_path, "fake.node")
