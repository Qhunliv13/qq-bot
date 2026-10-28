"""Read-only group inspection through an injected native OneBot action caller.

Only native allowlisted fields are reported. Pagination describes the list the
API returned, not proof of the complete QQ membership or user-defined bots.
"""

import re


# Public read fields deliberately exclude credentials and unrelated native data.
_INFO_FIELDS = ("group_id", "group_name", "member_count", "max_member_count")
_MEMBER_FIELDS = ("user_id", "nickname", "card", "role", "is_bot")


def schemas():
    """Describe the sole generic read tool without suggesting bot-name heuristics."""
    return [{"type": "function", "function": {
        "name": "qq_group_inspect",
        "description": (
            "Read native group information and a paginated native member list for an explicit group_id. "
            "Counts describe only the list returned by the API, not proof of complete QQ membership. "
            "User-defined bots cannot be inferred from nicknames or absent bot flags; bot_count is "
            "unknown unless every member in the nonempty native list has an explicit boolean is_bot."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {"anyOf": [
                    {"type": "integer", "minimum": 1},
                    {"type": "string", "pattern": "^[0-9]*[1-9][0-9]*$"},
                ]},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
            "required": ["group_id"],
            "additionalProperties": False,
        },
    }}]


def _arguments(args):
    """Validate the entire object before IO; IDs accept only positive ASCII digits."""
    if not isinstance(args, dict) or "group_id" not in args or set(args) - {"group_id", "offset", "limit"}:
        raise ValueError("invalid arguments")
    value = args["group_id"]
    # bool is an int subclass, but is never a meaningful caller identifier.
    if type(value) not in (str, int) or re.fullmatch(r"[0-9]+", str(value)) is None:
        raise ValueError("invalid group ID")
    group_id = int(value)
    if group_id <= 0:
        raise ValueError("invalid group ID")
    offset, limit = args.get("offset", 0), args.get("limit", 50)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("invalid pagination")
    return group_id, offset, limit


def _envelope(response, data_type):
    """Reject missing or contradictory envelopes rather than manufacturing native data."""
    if not isinstance(response, dict) or not {"status", "retcode", "data"}.issubset(response):
        return "unknown", None
    # Numeric strings, floats and booleans are not the native retcode contract.
    if type(response["retcode"]) is not int:
        return "unknown", None
    if response["status"] == "failed" and response["retcode"] != 0:
        return "failed", None
    if response["status"] != "ok" or response["retcode"] != 0 or not isinstance(response["data"], data_type):
        return "unknown", None
    return "ok", response["data"]


async def _read(call_action, action, group_id, data_type):
    """Contain native exceptions and messages behind a fixed failure classification."""
    try:
        response = await call_action(action, {"group_id": group_id, "no_cache": True})
    except Exception:
        # Never echo exception text: transports may include credentials or raw responses.
        return "unknown", None
    return _envelope(response, data_type)


async def inspect(args, call_action):
    """Read group then members, stopping on failure and preserving explicit field types.

    ``call_action`` is injected as ``await call_action(action, params)``; this
    module has no HTTP, cookies, owner permission gate, or guessed bot identity.
    Bot coverage is over the full native list, never just the requested page.
    """
    try:
        group_id, offset, limit = _arguments(args)
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid_arguments"}

    state, info = await _read(call_action, "get_group_info", group_id, dict)
    if state != "ok":
        return {"ok": False, "error": "group_info_read_failed", "outcome": state}
    state, members = await _read(call_action, "get_group_member_list", group_id, list)
    if state != "ok":
        return {"ok": False, "error": "group_members_read_failed", "outcome": state}
    if any(not isinstance(member, dict) for member in members):
        # Dropping malformed entries would fabricate counts/coverage for a partial list.
        return {"ok": False, "error": "group_members_read_failed", "outcome": "unknown"}

    page = members[offset:offset + limit]
    complete = bool(members) and all(type(member.get("is_bot")) is bool for member in members)
    return {
        "ok": True,
        "info": {key: info[key] for key in _INFO_FIELDS if key in info},
        "members": [{key: member[key] for key in _MEMBER_FIELDS if key in member} for member in page],
        "returned": len(page),
        "offset": offset,
        "more_available": offset + len(page) < len(members),
        "total_returned_by_api": len(members),
        "bot_count": sum(member["is_bot"] is True for member in members) if complete else None,
        "bot_classification_complete": complete,
    }
