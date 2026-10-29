"""Offline profile tests use real discovery and never import the bot entry point."""

import asyncio
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def modules():
    """Load leaf modules without optional bot dependencies or production startup."""
    loaded = []
    for name in ("tool_profile", "tool_discovery"):
        path = Path(__file__).resolve().parents[1] / "plugin" / (name + ".py")
        spec = importlib.util.spec_from_file_location("isolated_" + name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        loaded.append(module)
    return loaded


@pytest.fixture
def catalog(modules):
    """Build 106 entries with real discovery schemas and opaque native metadata."""
    native = [{"type": "function", "function": {
        "name": "native_" + str(index), "description": "原生 capability " + str(index),
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                       "required": ["path"], "additionalProperties": False},
        "strict": True}, "execution_environment": {"host": "explicit"}}
        for index in range(100)]
    # The reply helper uses its actual name; no reply implementation is needed.
    reply = {"type": "function", "function": {"name": "reply_now", "parameters": {}}}
    return native + modules[1].schema() + [reply]


def names(schemas):
    """Read names for order assertions without normalizing the schemas themselves."""
    return [schema["function"]["name"] for schema in schemas]


def describe(discovery, catalog, requested, tmp_path):
    """Produce an actual successful or failed result through existing dispatch."""
    return asyncio.run(discovery.dispatch(
        "tool_describe", {"names": requested}, catalog,
        skills_root=tmp_path / "unused", can_write=False))


def test_full_default_byte_equivalent(modules, catalog):
    """Default/full retain JSON byte content, field insertion order, and tool order."""
    encoded = json.dumps(catalog, ensure_ascii=False).encode("utf-8")
    for kwargs in ({}, {"mode": "full", "initial_tools": ["native_99"]}):
        profile = modules[0].ToolProfile(catalog, **kwargs)
        assert json.dumps(profile.schemas(), ensure_ascii=False).encode("utf-8") == encoded
        assert profile.full_catalog() == catalog
        assert not profile.observe("tool_describe", {"names": ["native_1"]}, {"ok": True})


def test_light_three_of_106_and_real_describe(modules, catalog, tmp_path):
    """All tools stay discoverable; describing adds exact native callable schemas."""
    profile = modules[0].ToolProfile(catalog, mode="light")
    assert len(catalog) == 106
    assert names(profile.schemas()) == ["tool_search", "tool_describe", "reply_now"]
    requested = ["native_92", "native_3"]
    result = describe(modules[1], profile.full_catalog(), requested, tmp_path)
    assert result["ok"] is True
    assert profile.observe("tool_describe", {"names": requested}, result)
    assert profile.schemas()[3:] == [catalog[3], catalog[92]]
    assert profile.full_catalog() == catalog


def test_stable_prefix_monotonic_deduplication(modules, catalog, tmp_path):
    """Each batch appends in catalog order without globally reordering prior tools."""
    profile = modules[0].ToolProfile(catalog, "light", ["native_90", "native_90"])
    assert names(profile.schemas())[0] == "native_90"
    for requested in (["native_60", "native_50", "native_60"], ["native_2"], ["native_90"]):
        before = profile.schemas()
        result = describe(modules[1], catalog, requested, tmp_path)
        profile.observe("tool_describe", {"names": requested}, result)
        assert profile.schemas()[:len(before)] == before
    assert names(profile.schemas())[-3:] == ["native_50", "native_60", "native_2"]
    assert len(names(profile.schemas())) == len(set(names(profile.schemas())))


def test_copies_and_identical_parameters_remain_native(modules, catalog):
    """Neither source nor returned nested objects can mutate the private snapshot."""
    original = deepcopy(catalog)
    profile = modules[0].ToolProfile(catalog, "light", ["native_0", "native_1"])
    catalog[0]["function"]["parameters"]["required"].clear()
    exposed = profile.schemas()
    exposed[0]["function"]["parameters"]["properties"]["path"]["type"] = "number"
    entire = profile.full_catalog()
    entire[1]["function"]["name"] = "forged"
    assert profile.schemas()[:2] == original[:2]
    assert profile.full_catalog() == original


@pytest.mark.parametrize("result", [None, "ok", {}, {"ok": False}, {"ok": 1},
                                    {"ok": "true"}, {"ok": True, "error": "denied"}])
def test_failed_or_malformed_results_do_not_promote(modules, catalog, result):
    """Success must be the actual protocol boolean, never a truthy error payload."""
    profile = modules[0].ToolProfile(catalog, "light")
    before = profile.schemas()
    assert not profile.observe("tool_describe", {"names": ["native_0"]}, result)
    assert profile.schemas() == before


@pytest.mark.parametrize("args", [None, {}, {"names": "native_0"}, {"names": []},
    {"names": [False]}, {"names": [[]]}, {"names": ["native_0", "unknown"]},
    {"names": ["native_0"] * 101}, {"names": ["native_0"], "extra": True}])
def test_invalid_observations_fail_closed(modules, catalog, args):
    """Unknown/malformed requests cannot partially promote even with forged success."""
    profile = modules[0].ToolProfile(catalog, "light")
    before = profile.schemas()
    assert not profile.observe("tool_describe", args, {"ok": True})
    assert profile.schemas() == before


def test_real_failure_and_untrusted_result_schemas(modules, catalog, tmp_path):
    """Results cannot introduce tools or override authoritative native definitions."""
    profile = modules[0].ToolProfile(catalog, "light")
    failed = describe(modules[1], catalog, ["native_0", "unknown"], tmp_path)
    assert not profile.observe("tool_describe", {"names": ["native_0"]}, failed)
    forged = {"ok": True, "tools": [{"name": "native_99", "schema": {"evil": True}}]}
    assert not profile.observe("tool_search", {"names": ["native_0"]}, forged)
    assert profile.observe("tool_describe", {"names": ["native_0"]}, forged)
    assert profile.schemas()[-1] == catalog[0]
    assert "native_99" not in names(profile.schemas())


@pytest.mark.parametrize("mode", [None, "", "lite", "FULL", [], 0])
def test_invalid_modes_rejected(modules, catalog, mode):
    """Misspelled configuration fails at construction, before any model calls."""
    with pytest.raises(ValueError):
        modules[0].ToolProfile(catalog, mode)


@pytest.mark.parametrize("initial", ["native_0", ["unknown"], [False], [[]]])
def test_invalid_initial_tools_rejected_in_both_modes(modules, catalog, initial):
    """Operator selections are explicit catalog names, not wildcard or policy rules."""
    for mode in ("full", "light"):
        with pytest.raises(ValueError):
            modules[0].ToolProfile(catalog, mode, initial)


def test_catalog_validation_and_optional_reply(modules, catalog):
    """Ambiguous schemas fail early; light cannot strand tools without discovery."""
    factory = modules[0].ToolProfile
    for bad in (catalog + [catalog[0]], [None], [{}],
                [{"type": "function", "function": {"name": []}}]):
        with pytest.raises(ValueError):
            factory(bad)
    for missing in ("tool_search", "tool_describe"):
        with pytest.raises(ValueError):
            factory([item for item in catalog if item["function"]["name"] != missing], "light")
    without_reply = [item for item in catalog if item["function"]["name"] != "reply_now"]
    assert names(factory(without_reply, "light").schemas()) == ["tool_search", "tool_describe"]
    assert factory([]).schemas() == []
