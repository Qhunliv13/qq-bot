"""Offline catalog metadata contracts, derived from native schema source via AST."""
import ast
from copy import deepcopy
from pathlib import Path
import re
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugin.qq_social import schema as social_schema  # noqa: E402
from plugin.workspace_tools import schema as workspace_schema  # noqa: E402
from plugin.tool_discovery import schema as discovery_schema  # noqa: E402
from plugin.delegation import schema as delegation_schema  # noqa: E402

from plugin.task_planning import schema as planning_schema  # noqa: E402
from plugin.interim_reply import schema as interim_schema  # noqa: E402
from plugin.image_search import schema as image_schema  # noqa: E402
from plugin.qq_requests import schema as request_schema  # noqa: E402
from plugin.message_transfer import schema as transfer_schema  # noqa: E402
from plugin.fact_tools import schemas as fact_schema  # noqa: E402

from plugin.group_inspection import schemas as group_inspection_schema  # noqa: E402

from plugin.fact_structure import structure_schema  # noqa: E402


def literal_with_structure(node, names=None):
    """Decode reviewed pure schema calls and literal metadata, never host code."""
    if isinstance(node, ast.Call):
        assert isinstance(node.func, ast.Name) and node.func.id == "structure_schema"
        assert not node.args and not node.keywords
        return structure_schema()
    if isinstance(node, ast.Name):
        # Only explicitly reviewed metadata bindings are resolved, never eval.
        assert names is not None and node.id in names
        return deepcopy(names[node.id])
    if isinstance(node, ast.Dict):
        return {literal_with_structure(key, names): literal_with_structure(value, names)
                for key, value in zip(node.keys, node.values)}
    if isinstance(node, ast.List):
        return [literal_with_structure(item, names) for item in node.elts]
    return ast.literal_eval(node)

IMPORTED_BUILDERS = (social_schema, workspace_schema, discovery_schema, delegation_schema, planning_schema, interim_schema, image_schema, request_schema, transfer_schema, fact_schema, group_inspection_schema)
from plugin.tool_catalog import NATIVE_TOOL_COUNT, TOOL_DESCRIPTIONS, neutralize_schemas  # noqa: E402


def native_schema_metadata(tree):
    """Resolve only reviewed schema bindings once for all AST-only test readers."""
    from plugin.vm_pointer_runtime import MAX_DIMENSION, MAX_DURATION_MS, MAX_POINTS
    metadata = {"MAX_DIMENSION": MAX_DIMENSION, "MAX_DURATION_MS": MAX_DURATION_MS,
                "MAX_POINTS": MAX_POINTS}
    method = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef) and node.name == "_tool_schemas")
    path_schema = next(node.value for node in method.body
                       if isinstance(node, ast.Assign) and any(
                           isinstance(target, ast.Name) and target.id == "path_action"
                           for target in node.targets))
    metadata["path_action"] = literal_with_structure(path_schema, metadata)
    return metadata


@pytest.fixture
def native_schemas():
    """Read literal schema declarations without importing the AstrBot host module."""
    path = Path(__file__).resolve().parents[1] / "plugin" / "main.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    methods = {
        node.name: node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name in {"_tool_schemas", "_task_tool_schemas"}
    }
    assert set(methods) == {"_tool_schemas", "_task_tool_schemas"}
    metadata = native_schema_metadata(tree)
    schemas = []
    for method_name in ("_tool_schemas", "_task_tool_schemas"):
        # Only literal _f declarations are inspected; no host code is compiled,
        # imported or evaluated, even when the central return gets wrapped.
        calls = sorted(
            (node for node in ast.walk(methods[method_name])
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
             and node.func.id == "_f"),
            key=lambda node: node.lineno,
        )
        for call in calls:
            args = [literal_with_structure(arg, metadata) for arg in call.args]
            schemas.append({"type": "function", "function": {
                "name": args[0], "description": args[1],
                "parameters": {"type": "object",
                               "properties": args[2] if len(args) > 2 else {},
                               "required": args[3] if len(args) > 3 else []},
            }})
    # The native builder explicitly appends this import-owned schema list.
    for builder in IMPORTED_BUILDERS:
        schemas.extend(builder())
    return schemas


def test_exact_coverage_of_all_native_tool_names(native_schemas):
    """A new or removed native tool requires an explicit metadata review."""
    names = {schema["function"]["name"] for schema in native_schemas}
    assert len(names) == NATIVE_TOOL_COUNT == 110
    assert len(TOOL_DESCRIPTIONS) == NATIVE_TOOL_COUNT
    assert set(TOOL_DESCRIPTIONS) == names
    assert all(isinstance(text, str) and text.strip() for text in TOOL_DESCRIPTIONS.values())


def test_import_owned_descriptions_are_shared_without_a_second_definition():
    """Imported capabilities own exact prose and parameter metadata together."""
    names = []
    for builder in IMPORTED_BUILDERS:
        for item in builder():
            function = item["function"]
            names.append(function["name"])
            assert TOOL_DESCRIPTIONS[function["name"]] == function["description"]
    assert len(names) == len(set(names)) == 40


def test_all_descriptions_are_neutral_metadata(native_schemas):
    """All advertised native descriptions avoid persona and forced-use wording."""
    for schema in neutralize_schemas(native_schemas):
        description = schema["function"]["description"]
        assert not re.search(r"她|你|主人|喵|✓|✗|先.*才|必须", description)
        assert description == TOOL_DESCRIPTIONS[schema["function"]["name"]]


def test_native_shapes_parameters_and_required_fields_are_preserved(native_schemas):
    """Only function descriptions change; last native declarations own contracts."""
    original = deepcopy(native_schemas)
    latest = {schema["function"]["name"]: schema for schema in native_schemas}
    result = neutralize_schemas(native_schemas)
    assert len(result) == NATIVE_TOOL_COUNT
    assert list(latest) == [schema["function"]["name"] for schema in result]
    for schema in result:
        name = schema["function"]["name"]
        expected = deepcopy(latest[name])
        expected["function"]["description"] = TOOL_DESCRIPTIONS[name]
        assert schema == expected
        parameters = schema["function"]["parameters"]
        assert set(parameters["required"]) <= set(parameters["properties"])
        # Preserve imported native metadata too, including operation-ID descriptions.
        assert parameters == latest[name]["function"]["parameters"]
        if name not in {item["function"]["name"] for builder in IMPORTED_BUILDERS for item in builder()}:
            assert all("description" not in prop for prop in parameters["properties"].values())
    assert native_schemas == original


def test_last_duplicate_wins_without_reordering_or_merging(native_schemas):
    """Later group-aware wake declarations replace, not merge with, older ones."""
    result = neutralize_schemas(native_schemas)
    by_name = {schema["function"]["name"]: schema for schema in result}
    for name in ("wake_list", "wake_add", "wake_remove"):
        declarations = [schema for schema in native_schemas if schema["function"]["name"] == name]
        assert len(declarations) == 2
        assert by_name[name]["function"]["parameters"] == declarations[-1]["function"]["parameters"]
        assert "group" in by_name[name]["function"]["parameters"]["properties"]


def test_deep_copy_and_idempotence(native_schemas):
    """Repeated normalization is stable and callers cannot mutate source schemas."""
    original = deepcopy(native_schemas)
    result = neutralize_schemas(native_schemas)
    second = neutralize_schemas(result)
    assert second == result
    assert second is not result
    result[0]["function"]["parameters"]["properties"]["query"]["type"] = "number"
    result[0]["function"]["parameters"]["required"].append("extra")
    assert second[0]["function"]["parameters"]["properties"]["query"]["type"] == "string"
    assert native_schemas == original


def test_unknown_names_and_nested_metadata_are_preserved():
    """No inferred descriptions or phrase rewriting may alter extension schemas."""
    schema = {"type": "function", "extension": {"values": [1]}, "function": {
        "name": "extension_tool", "description": "Read the configured extension state.",
        "strict": True, "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {"payload": {"type": "object", "description": "Payload metadata.",
                                       "properties": {"mode": {"enum": ["必须", "她"]}}}},
            "required": ["payload"],
        },
    }}
    result = neutralize_schemas([schema])
    assert result == [schema]
    result[0]["extension"]["values"].append(2)
    result[0]["function"]["parameters"]["properties"]["payload"]["properties"]["mode"]["enum"].append("x")
    assert schema["extension"]["values"] == [1]
    assert schema["function"]["parameters"]["properties"]["payload"]["properties"]["mode"]["enum"] == ["必须", "她"]


def test_missing_descriptions_are_only_added_for_known_names():
    """Missing metadata is not a reason to guess an unknown tool's capability."""
    known = {"function": {"name": "vm_shot", "parameters": {"required": []}}}
    unknown = {"function": {"name": "extension_tool"}}
    result = neutralize_schemas([known, unknown])
    assert result[0]["function"]["description"] == TOOL_DESCRIPTIONS["vm_shot"]
    assert "description" not in result[1]["function"]
    assert "description" not in known["function"]


@pytest.mark.parametrize("schema", [None, [], {}, {"function": None},
                                     {"function": {}}, {"function": {"name": ""}},
                                     {"function": {"name": "  "}},
                                     {"function": {"name": 123}}])
def test_missing_or_invalid_function_name_raises(schema):
    """Invalid identities fail explicitly instead of dropping or renaming tools."""
    with pytest.raises(ValueError):
        neutralize_schemas([schema])


def test_empty_and_iterable_inputs():
    """The public helper returns a list for empty input and schema iterables."""
    assert neutralize_schemas([]) == []
    assert neutralize_schemas(iter([{"function": {"name": "extension_tool"}}])) == [
        {"function": {"name": "extension_tool"}},
    ]


def test_click_confirm_requires_matching_visual_checkpoint(native_schemas):
    """Metadata must require the exact model-visible ticket, pixels and location."""
    schema = next(schema for schema in native_schemas if schema["function"]["name"] == "vm_click_confirm")
    assert schema["function"]["parameters"]["required"] == [
        "ticket", "x", "y", "screen_sha256", "viewed"]
    assert "一次性 ticket" in TOOL_DESCRIPTIONS["vm_click_confirm"]
