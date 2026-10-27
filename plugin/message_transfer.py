"""Verified native QQ forwarding and scope-private copy/paste without text fallbacks.

The trusted parent owns source visibility, destination policy and the action
bridge. No model-supplied sender, segments or clipboard scope is accepted. Every
send intent is durably claimed before lookup and never retried automatically.
"""

import asyncio
import hashlib
import json

from .qq_delivery import receipt
from .qq_native_actions import _identifier, _status
from .qq_social import Journal


_TOOLS = frozenset({"qq_forward_messages", "qq_copy_message", "qq_paste_message"})


def schema():
    """Expose three explicit native operations without a hidden history-count limit."""
    identifier = {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "string", "pattern": "^[0-9]*[1-9][0-9]*$"}]}
    operation = {"type": "string", "minLength": 1, "description": "Stable ID for this exact intent; reuse on retries, including unknown or partial delivery."}
    definitions = [
        ("qq_forward_messages", "Forward verified source-group messages to the same or another authorized group. Choose exactly one of message_ids or count. Merged preserves native authors through reference nodes; individual uses native single forwarding. Never retries unknown sends.", {
            "source_group_id": identifier, "target_group_id": identifier,
            "message_ids": {"type": "array", "minItems": 1, "items": identifier},
            "count": {"type": "integer", "minimum": 1, "description": "Requested native history count; no arbitrary business cap or silent truncation."},
            "before_seq": {**identifier, "description": "Optional native history anchor, mapped to message_seq. Native platform determines anchor inclusion; this is not a guaranteed exclusive-before filter. Only with count."},
            "mode": {"type": "string", "enum": ["merged", "individual"], "default": "merged"},
            "operation_id": operation,
        }, ["source_group_id", "target_group_id", "operation_id"]),
        ("qq_copy_message", "Copy one source-verified native group message into this conversation's private clipboard. Returns metadata only and sends nothing.", {
            "source_group_id": identifier, "message_id": identifier,
        }, ["source_group_id", "message_id"]),
        ("qq_paste_message", "Paste an owned clipboard's original native segments into an authorized group as the bot, not as the original sender. No text conversion or automatic forwarding.", {
            "clipboard_id": {"type": "string", "minLength": 1}, "target_group_id": identifier, "operation_id": operation,
        }, ["clipboard_id", "target_group_id", "operation_id"]),
    ]
    result = []
    for name, description, properties, required in definitions:
        parameters = {"type": "object", "properties": properties, "required": required, "additionalProperties": False}
        if name == "qq_forward_messages":
            # oneOf rejects both and neither; runtime validation also enforces it.
            parameters["oneOf"] = [{"required": ["message_ids"], "not": {"required": ["count"]}}, {"required": ["count"], "not": {"required": ["message_ids"]}}]
        result.append({"type": "function", "function": {"name": name, "description": description, "parameters": parameters}})
    return result


def _error(code, *, outcome="failed", delivery_message_ids=()):
    """Return only fixed diagnostics and confirmed IDs, never native content or URLs."""
    return {"ok": False, "delivered": False, "error": code, "outcome": outcome, "delivery_message_ids": list(delivery_message_ids)}


def _validate(name, args):
    """Reject coercions, ambiguous selectors and unknown fields before any native IO."""
    definitions = {item["function"]["name"]: item["function"]["parameters"] for item in schema()}
    if name not in _TOOLS or type(args) is not dict:
        raise ValueError("invalid tool arguments")
    spec = definitions[name]
    if set(args) - set(spec["properties"]) or not set(spec["required"]).issubset(args):
        raise ValueError("unexpected or missing arguments")
    normalized = dict(args)
    for key in ("source_group_id", "target_group_id", "message_id", "before_seq"):
        if key in normalized:
            normalized[key] = _identifier(normalized[key])
    for key in ("operation_id", "clipboard_id"):
        if key in normalized and (not isinstance(normalized[key], str) or not normalized[key].strip()):
            raise ValueError("nonempty identifier required")
    if name == "qq_forward_messages":
        if ("message_ids" in args) == ("count" in args):
            raise ValueError("exactly one message selector required")
        if "message_ids" in args:
            if type(args["message_ids"]) is not list or not args["message_ids"] or "before_seq" in args:
                raise ValueError("explicit message IDs required")
            normalized["message_ids"] = [_identifier(value) for value in args["message_ids"]]
            if len(set(normalized["message_ids"])) != len(normalized["message_ids"]):
                raise ValueError("duplicate message IDs")
        elif type(args["count"]) is not int or args["count"] <= 0:
            raise ValueError("positive integer count required")
        normalized.setdefault("mode", "merged")
        if normalized["mode"] not in ("merged", "individual"):
            raise ValueError("invalid mode")
    return normalized


def _allowed(callback, group_id):
    """Fail closed unless the trusted synchronous policy explicitly returns True."""
    try:
        return callable(callback) and callback(group_id) is True
    except Exception:
        return False


def _record(data, source_group_id, expected_id=None):
    """Require actual native group provenance and intact structured message segments."""
    if type(data) is not dict or data.get("message_type") != "group":
        raise ValueError("source message is not a group message")
    if _identifier(data.get("group_id")) != source_group_id:
        raise ValueError("source group mismatch")
    message_id = _identifier(data.get("message_id"))
    if expected_id is not None and message_id != expected_id:
        raise ValueError("source message mismatch")
    segments = data.get("message")
    if type(segments) is not list or not segments:
        raise ValueError("native segments required")
    if any(type(segment) is not dict or type(segment.get("type")) is not str or not segment["type"] or type(segment.get("data")) is not dict for segment in segments):
        raise ValueError("malformed native segments")
    # Round-trip ensures finite JSON while preserving every native segment field.
    snapshot = json.loads(json.dumps(data, ensure_ascii=True, allow_nan=False))
    snapshot.update(source_group_id=source_group_id, source_message_id=message_id)
    return snapshot


async def _get_message(source_group_id, message_id, call_action):
    """Fetch an actual native message and verify its group instead of trusting a local ID."""
    response = await call_action("get_msg", {"message_id": message_id})
    if _status(response) != "confirmed":
        raise ValueError("source lookup unconfirmed")
    return _record(response["data"], source_group_id, message_id)


async def _select(args, call_action):
    """Resolve every selected source before the first externally visible mutation."""
    source = args["source_group_id"]
    if "message_ids" in args:
        # Explicit IDs preserve caller order; history mode uses native chronology.
        return [await _get_message(source, message_id, call_action) for message_id in args["message_ids"]]
    # Installed history schema requires string group/sequence identifiers even
    # though get_msg and merged sending also accept numeric message IDs.
    params = {"group_id": str(source), "count": args["count"], "reverseOrder": False}
    if "before_seq" in args:
        params["message_seq"] = str(args["before_seq"])
    response = await call_action("get_group_msg_history", params)
    if _status(response) != "confirmed" or type(response["data"]) is not dict:
        raise ValueError("history lookup unconfirmed")
    messages = response["data"].get("messages")
    if type(messages) is not list or not messages or len(messages) > args["count"]:
        raise ValueError("invalid native history")
    selected = [_record(message, source) for message in messages]
    if len({item["source_message_id"] for item in selected}) != len(selected):
        raise ValueError("duplicate native history IDs")
    if all(type(item.get("time")) in (int, float) for item in selected):
        # Native epochs provide chronology without interpreting opaque IDs or
        # inventing times. Stable sorting retains native order for timestamp ties.
        selected.sort(key=lambda item: item["time"])
    return selected


async def _deliver(action, payload, call_action):
    """Submit exactly one send and distinguish native rejection from ambiguous transport."""
    try:
        response = await call_action(action, payload)
    except (Exception, asyncio.CancelledError):
        return _error("write_outcome_unknown", outcome="unknown")
    state = _status(response)
    if state != "confirmed":
        return _error("onebot_action_failed" if state == "failed" else "delivery_not_confirmed", outcome="failed" if state == "failed" else "unknown")
    if action == "forward_group_single_msg" and response["data"] is None:
        # Installed native single forwarding returns null: acceptance is not ID
        # proof of delivery. Keep this distinction while allowing the next item.
        return {"ok": True, "accepted": True, "delivered": False, "outcome": "accepted", "action": action}
    result = receipt(response, action=action)
    if not result["delivered"]:
        return _error(result["error"], outcome="failed" if _status(response) == "failed" else "unknown")
    result.update(accepted=True, outcome="delivered")
    return result


async def _send_selected(args, selected, call_action, can_send_target, *, paste=False):
    """Preserve native forwarding semantics and stop at the first failure or policy change."""
    target = args["target_group_id"]
    if paste:
        sends = [("send_group_msg", {"group_id": target, "message": selected[0]["message"]})]
    elif args["mode"] == "merged":
        # Reference-only nodes preserve real native authors; no forged uin/name.
        sends = [("send_group_forward_msg", {"group_id": target, "messages": [{"type": "node", "data": {"id": str(item["source_message_id"])}} for item in selected]})]
    else:
        # Installed schema requires the optional group destination to be a string.
        sends = [("forward_group_single_msg", {"group_id": str(target), "message_id": item["source_message_id"]}) for item in selected]
    delivered, results = [], []
    for action, payload in sends:
        # Recheck the destination before each send, including during a long batch.
        if not _allowed(can_send_target, target):
            failure = _error("target_permission_denied", outcome="partial" if results else "failed", delivery_message_ids=delivered)
            failure.update(results=results, accepted_count=len(results))
            return failure
        result = await _deliver(action, payload, call_action)
        if not result.get("accepted"):
            outcome = "partial" if results and result["outcome"] != "unknown" else result["outcome"]
            failure = _error(result["error"], outcome=outcome, delivery_message_ids=delivered)
            failure.update(results=results + [result], accepted_count=len(results))
            return failure
        results.append(result)
        if result["delivered"]:
            delivered.append(result["message_id"])
    confirmed = len(delivered) == len(sends)
    return {"ok": True, "accepted": True, "delivered": confirmed, "outcome": "delivered" if confirmed else "accepted", "delivery_message_ids": delivered, "accepted_count": len(results), "results": results, "selected_count": len(selected), "target_group_id": target}


async def dispatch(name, args, call_action, *, scope, store, audit_path, can_read_source, can_send_target):
    """Execute validated group-only tools with scoped clipboard and durable at-most-once IO.

    Callbacks are trusted synchronous group-ID predicates, not model arguments.
    The parent must enforce current policy/autonomy in can_send_target. All native
    replies remain private except fixed diagnostics and confirmed identifiers.
    """
    try:
        args = _validate(name, args)
        if not isinstance(scope, str) or not scope.strip() or not callable(call_action):
            raise ValueError("trusted dispatch context required")
    except (ValueError, TypeError):
        return _error("invalid_arguments")
    if name != "qq_copy_message" and not _allowed(can_send_target, args["target_group_id"]):
        return _error("target_permission_denied")
    if "source_group_id" in args and not _allowed(can_read_source, args["source_group_id"]):
        return _error("source_permission_denied")
    if name == "qq_copy_message":
        try:
            copied = await _get_message(args["source_group_id"], args["message_id"], call_action)
            saved = store.put(scope, copied)
            return {"ok": True, "clipboard_id": saved["clipboard_id"], "source_group_id": copied["source_group_id"], "source_message_id": copied["source_message_id"]}
        except (Exception, asyncio.CancelledError):
            return _error("source_copy_failed")
    if audit_path is None:
        return _error("journal_unavailable")
    # Namespace by exact scope using canonical JSON, avoiding delimiter collisions.
    operation = "qq_message_transfer:" + json.dumps([scope, args["operation_id"]], ensure_ascii=True, separators=(",", ":"))
    digest = hashlib.sha256(json.dumps([name, args], sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    journal = Journal(audit_path)
    try:
        prior = journal.claim(operation, digest)
    except Exception:
        return _error("journal_unavailable")
    if prior is not None:
        return prior
    try:
        if name == "qq_paste_message":
            copied = store.get(scope, args["clipboard_id"])
            if copied is None:
                result = _error("clipboard_not_found")
            elif not _allowed(can_read_source, _identifier(copied.get("source_group_id"))):
                result = _error("source_permission_denied")
            else:
                selected = [_record(copied, _identifier(copied["source_group_id"]), _identifier(copied["source_message_id"]))]
                result = await _send_selected(args, selected, call_action, can_send_target, paste=True)
        else:
            selected = await _select(args, call_action)
            result = await _send_selected(args, selected, call_action, can_send_target)
    except (Exception, asyncio.CancelledError):
        # Lookup failure precedes sends; send uncertainty is handled by _deliver.
        result = _error("source_lookup_failed")
    try:
        journal.finish(operation, digest, result)
    except Exception:
        return _error("journal_finish_unknown", outcome="unknown", delivery_message_ids=result.get("delivery_message_ids", ()))
    return result
