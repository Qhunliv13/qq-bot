"""Fixed native-load control tests never load QQ or execute native code themselves."""

import copy
import json
from unittest.mock import Mock

import pytest

from tools import qq_isolated_runtime_probe as probe


def record():
    """Return synthetic descriptor evidence that must not promote QQ readiness."""
    return {"versions": {"node": "18.19.1", "modules": "109", "napi": "9"},
            "candidates": [{"candidate": "wrapper", "loaded": False,
                            "dlopenFailed": True, "undefinedSymbol": True,
                            "missingQqMagic": False, "missingSharedLibrary": False,
                            "moduleVersionMismatch": False, "notSelfRegister": False},
                           {"candidate": "major", "loaded": True,
                            "exports": [{"name": "load", "type": "function"}],
                            "exportsTruncated": False}]}


def encoded(value=None, nonce="nonce"):
    """Encode only fixture evidence with the control-specific nonce marker."""
    return ("QQ_NATIVE_" + nonce + ":" + json.dumps(record() if value is None else value) + "\n").encode()


def test_parser_accepts_bounded_control_only():
    """Successful parsing produces system Node evidence, never a readiness flag."""
    result = probe.parse_native_output(b"discarded log\n" + encoded(), "nonce")
    assert result["native_candidates"][1]["exports"] == [{"name": "load", "type": "function"}]
    assert set(result) == {"system_node_versions", "native_candidates"}


@pytest.mark.parametrize("data", [b"absent", encoded(nonce="other"), encoded() * 2,
                                  b"x" * (probe.OUTPUT_LIMIT + 1), b"QQ_NATIVE_nonce:{broken\n",
                                  encoded({"versions": {}, "candidates": []})],
                         ids=["absent", "wrong_nonce", "duplicate", "oversize", "malformed", "incomplete"])
def test_parser_rejects_untrusted_records(data):
    """Wrong nonces, oversize, malformed and incomplete records cannot become evidence."""
    with pytest.raises(probe.ProbeError):
        probe.parse_native_output(data, "nonce")


@pytest.mark.parametrize("mutation", ["bool", "candidate", "exporttype", "exportname", "excess", "extra", "versiontype", "typelist"])
def test_parser_rejects_wrong_types_and_fields(mutation):
    """Schema restrictions prevent hostile module output surfacing arbitrary diagnostics."""
    value = copy.deepcopy(record())
    if mutation == "bool":
        value["candidates"][0]["loaded"] = 1
    elif mutation == "candidate":
        value["candidates"][0]["candidate"] = "elsewhere"
    elif mutation == "exporttype":
        value["candidates"][1]["exports"][0]["type"] = "secret"
    elif mutation == "exportname":
        value["candidates"][1]["exports"][0]["name"] = "/private/path"
    elif mutation == "excess":
        value["candidates"][1]["exports"] *= 33
    elif mutation == "extra":
        value["safe_to_login"] = True
    elif mutation == "versiontype":
        value["versions"]["node"] = 18
    else:
        value["candidates"][1]["exports"][0]["type"] = []
    with pytest.raises(probe.ProbeError):
        probe.parse_native_output(encoded(value), "nonce")


def test_fixed_script_never_calls_exports_or_getters():
    """No caller-supplied command, loader override, application entry or service call exists."""
    script = probe.native_load_script("nonce")
    assert "Object.getOwnPropertyDescriptor" in script
    assert "typeof d.value" in script
    assert "QQ_NATIVE_nonce:" in script
    for forbidden in ("mod.load(", "mod[name]", "process.env", "application.asar", "--no-sandbox", "eval("):
        assert forbidden not in script


def test_cli_controls_are_mutually_exclusive():
    """CLI cannot combine trusted userns control with native execution."""
    with pytest.raises(SystemExit) as exc:
        probe.main(["--qq-root", "/unused", "--userns-control", "--native-load-control"])
    assert exc.value.code == 2


def test_api_controls_refuse_before_read(monkeypatch):
    """API callers have the same fail-closed exclusivity as CLI callers."""
    monkeypatch.setattr(probe, "validate_root", Mock(side_effect=AssertionError))
    report = probe.probe("unused", userns_control=True, native_load_control=True)
    assert report["errors"] == ["mutually_exclusive_controls"]
    assert report["native_binding_verified"] is False


def test_cli_default_native_does_not_execute(monkeypatch, capsys):
    """Selecting native control is not execution consent, nor QQ readiness."""
    monkeypatch.setattr(probe.sys, "platform", "linux")
    monkeypatch.setattr(probe, "validate_root", Mock(return_value="unused"))
    runner = Mock(side_effect=AssertionError)
    monkeypatch.setattr(probe.subprocess, "run", runner)
    assert probe.main(["--qq-root", "/unused", "--native-load-control"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "system_node_native_load_control"
    for key in ("native_control_executed", "native_load_control_verified", "runtime_executed", "runtime_versions_verified", "native_binding_verified", "safe_to_login", "production_cutover_authorized"):
        assert result[key] is False
    runner.assert_not_called()
