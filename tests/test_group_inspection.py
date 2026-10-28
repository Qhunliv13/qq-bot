"""Group inspection contracts use injected fixtures, never live QQ or HTTP."""

import asyncio
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def inspection():
    """Load the independent read tool without importing the production runtime."""
    path = Path(__file__).resolve().parents[1] / "plugin" / "group_inspection.py"
    spec = importlib.util.spec_from_file_location("isolated_group_inspection", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeBridge:
    """Record exact calls while replaying native envelopes or transport failures."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, action, params):
        """Return a fixture without IO and raise supplied exceptions unchanged."""
        self.calls.append((action, params))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def accepted(data):
    """Supply all mandatory native success-envelope fields."""
    return {"status": "ok", "retcode": 0, "data": data}


def invoke(inspection, bridge, args=None):
    """Run one isolated async read with a convenient explicit default group ID."""
    return asyncio.run(inspection.inspect({"group_id": "123"} if args is None else args, bridge))


def test_schema_is_generic_and_strict(inspection):
    """Only the requested group and bounded pagination may enter the tool."""
    schemas = inspection.schemas()
    assert len(schemas) == 1
    function = schemas[0]["function"]
    assert function["name"] == "qq_group_inspect"
    spec = function["parameters"]
    assert spec["required"] == ["group_id"]
    assert spec["additionalProperties"] is False
    assert set(spec["properties"]) == {"group_id", "offset", "limit"}
    assert spec["properties"]["offset"]["default"] == 0
    assert spec["properties"]["limit"] == {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}
    assert "cannot be inferred" in function["description"]


@pytest.mark.parametrize("args", [
    None, [], {}, {"group_id": True}, {"group_id": False}, {"group_id": 0},
    {"group_id": -1}, {"group_id": 1.0}, {"group_id": "0"}, {"group_id": "000"},
    {"group_id": "+123"}, {"group_id": " 123"}, {"group_id": "123\n"},
    {"group_id": "１２３"}, {"group_id": "١٢٣"}, {"group_id": "1e2"},
    {"group_id": {}}, {"group_id": "123", "unknown": True},
    {"group_id": 123, "offset": -1}, {"group_id": 123, "offset": True},
    {"group_id": 123, "offset": "0"}, {"group_id": 123, "offset": 0.0},
    {"group_id": 123, "limit": 0}, {"group_id": 123, "limit": 201},
    {"group_id": 123, "limit": True}, {"group_id": 123, "limit": "50"},
    {"group_id": 123, "limit": 1.0},
])
def test_invalid_arguments_never_call_native(inspection, args):
    """Validation rejects coercion, unexpected keys and invalid ranges before calls."""
    bridge = FakeBridge([])
    result = asyncio.run(inspection.inspect(args, bridge))
    assert result == {"ok": False, "error": "invalid_arguments"}
    assert bridge.calls == []


@pytest.mark.parametrize("group_id", [123, "123", "000123"])
def test_native_fields_only_and_no_nickname_bot_inference(inspection, group_id):
    """Bot-like labels and roles do not supply absent native bot classification."""
    info = {"group_id": "123", "group_name": "Fixture", "member_count": 999, "max_member_count": 2000, "cookie": "secret"}
    members = [
        {"user_id": "456", "nickname": "AI robot bot", "card": "机器人", "role": "owner", "token": "secret"},
        {"user_id": 789, "nickname": "human", "is_bot": False, "age": 20},
    ]
    bridge = FakeBridge([accepted(info), accepted(members)])
    result = invoke(inspection, bridge, {"group_id": group_id})
    assert bridge.calls == [
        ("get_group_info", {"group_id": 123, "no_cache": True}),
        ("get_group_member_list", {"group_id": 123, "no_cache": True}),
    ]
    assert result == {
        "ok": True,
        "info": {key: info[key] for key in ("group_id", "group_name", "member_count", "max_member_count")},
        "members": [
            {"user_id": "456", "nickname": "AI robot bot", "card": "机器人", "role": "owner"},
            {"user_id": 789, "nickname": "human", "is_bot": False},
        ],
        "returned": 2, "offset": 0, "more_available": False,
        "total_returned_by_api": 2, "bot_count": None, "bot_classification_complete": False,
    }


@pytest.mark.parametrize("offset,limit,returned,more", [(0, 1, 1, True), (1, 1, 1, True), (2, 1, 1, False), (3, 200, 0, False), (10, 1, 0, False)])
def test_pagination_and_full_native_bool_coverage(inspection, offset, limit, returned, more):
    """Pagination counts actual entries; bot count covers the native list, not page."""
    members = [{"user_id": i, "is_bot": flag} for i, flag in enumerate([True, False, True], start=1)]
    bridge = FakeBridge([accepted({}), accepted(members)])
    result = invoke(inspection, bridge, {"group_id": 123, "offset": offset, "limit": limit})
    assert result["members"] == members[offset:offset + limit]
    assert result["returned"] == returned
    assert result["offset"] == offset
    assert result["more_available"] is more
    assert result["total_returned_by_api"] == 3
    assert result["bot_count"] == 2
    assert result["bot_classification_complete"] is True
    assert result["info"] == {}  # No fallback ID or invented member count.


@pytest.mark.parametrize("members", [[], [{"nickname": "robot"}], [{"is_bot": None}], [{"is_bot": 1}], [{"is_bot": "true"}], [{"is_bot": True}, {}]])
def test_unknown_classification_preserves_explicit_native_types(inspection, members):
    """Empty, missing or nonboolean flags never imply a complete bot census."""
    bridge = FakeBridge([accepted({}), accepted(members)])
    result = invoke(inspection, bridge)
    assert result["bot_count"] is None
    assert result["bot_classification_complete"] is False
    assert result["members"] == members


@pytest.mark.parametrize("response,outcome", [
    (None, "unknown"), ({}, "unknown"), ({"data": {}}, "unknown"),
    ({"status": "ok", "data": {}}, "unknown"),
    ({"status": "ok", "retcode": False, "data": {}}, "unknown"),
    ({"status": "ok", "retcode": "0", "data": {}}, "unknown"),
    ({"status": "ok", "retcode": 0.0, "data": {}}, "unknown"),
    ({"status": "ok", "retcode": 1, "data": {}}, "unknown"),
    ({"status": "failed", "retcode": 0, "data": {}}, "unknown"),
    ({"status": "failed", "retcode": 1, "data": None, "message": "secret"}, "failed"),
    (accepted(None), "unknown"), (accepted([]), "unknown"),
    (RuntimeError("secret cookies raw response"), "unknown"),
])
def test_info_failure_stops_members_call_and_sanitizes_errors(inspection, response, outcome):
    """Native failure/unknown is not transformed into fake successful inspection."""
    bridge = FakeBridge([response])
    assert invoke(inspection, bridge) == {"ok": False, "error": "group_info_read_failed", "outcome": outcome}
    assert len(bridge.calls) == 1


@pytest.mark.parametrize("response,outcome", [
    (None, "unknown"), ({"data": []}, "unknown"), (accepted({}), "unknown"),
    (accepted([None]), "unknown"), (accepted([{"user_id": 1}, "bad"]), "unknown"),
    ({"status": "failed", "retcode": 100, "data": None}, "failed"),
    (RuntimeError("secret"), "unknown"),
])
def test_member_failure_is_fixed_and_never_returns_partial_data(inspection, response, outcome):
    """Malformed member entries cannot silently disappear from counts/coverage."""
    bridge = FakeBridge([accepted({"group_name": "private"}), response])
    assert invoke(inspection, bridge) == {"ok": False, "error": "group_members_read_failed", "outcome": outcome}
    assert len(bridge.calls) == 2


def test_default_page_is_fifty_and_all_false_is_explicit_zero(inspection):
    """Only full boolean coverage permits zero; default page remains bounded."""
    members = [{"user_id": i, "is_bot": False} for i in range(60)]
    result = invoke(inspection, FakeBridge([accepted({}), accepted(members)]))
    assert result["returned"] == 50
    assert result["total_returned_by_api"] == 60
    assert result["more_available"] is True
    assert result["bot_count"] == 0
    assert result["bot_classification_complete"] is True
