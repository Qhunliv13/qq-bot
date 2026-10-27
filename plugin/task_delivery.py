"""Pure, conservative delivery evidence shared by tool execution and scheduling.

Only reviewed outbound tool results count. Callers own tool-name history and
persistence; this module performs no IO, retries, content inference or logging.
"""

# These tools have reviewed outbound semantics; names resembling them do not count.
_OUTBOUND_TOOLS = frozenset({"qq_send", "reply_now", "send_file", "send_shot", "vm_send_shot",
                             "qq_forward_messages", "qq_paste_message"})
# An exception/empty acknowledgement can occur after sending, so retry is unsafe.
_UNKNOWN_ERRORS = frozenset({"transport_exception", "send_exception", "delivery_not_confirmed"})


def fresh() -> dict:
    """Return independent JSON-safe evidence, never implying that quiet work sent."""
    return {
        "delivery_attempted": False,
        "delivery_confirmed": False,
        "delivery_failed": False,
        "delivery_unknown": False,
        "delivery_message_ids": [],
        "last_tools": [],
    }


def _message_id(value) -> str | None:
    """Keep positive native IDs only, excluding booleans and Unicode lookalikes."""
    if type(value) is int and value > 0:
        return str(value)
    if (isinstance(value, str) and value and value.isascii() and value.isdigit()
            and any(char != "0" for char in value)):
        return value
    return None


def _receipt_ids(result: dict, name: str) -> list[str]:
    """Copy reviewed receipt fields only; qq_send requires its native top-level ID."""
    candidates = [result.get("message_id")]
    if name != "qq_send":
        # Media adapters can preserve the native receipt under data; TaskEvent
        # transports can provide several receipts for a single decoded body.
        data = result.get("data")
        if isinstance(data, dict):
            candidates.append(data.get("message_id"))
        message_ids = result.get("delivery_message_ids")
        if isinstance(message_ids, list):
            candidates.extend(message_ids)
    ids = []
    for candidate in candidates:
        message_id = _message_id(candidate)
        if message_id is not None and message_id not in ids:
            ids.append(message_id)
    return ids


def observe(outcome: dict, name: str, result) -> None:
    """Accumulate reviewed result evidence into only the caller-provided ledger.

    Flags are sticky across attempts: a later receipt cannot erase an earlier
    failure or ambiguity. Caller-owned last_tools is deliberately not appended
    here, preventing duplicate history when _execute_tool records all names.
    """
    if not isinstance(name, str) or name not in _OUTBOUND_TOOLS:
        return
    payload = result if isinstance(result, dict) else {}
    error = payload.get("error")
    if (name == "reply_now" and payload.get("ok") is True
            and payload.get("delivered") is False and not error):
        # The shared interim helper explicitly uses this shape for silence.
        return
    outcome["delivery_attempted"] = True
    if (isinstance(error, str) and error in _UNKNOWN_ERRORS) or payload.get("exception"):
        # Do not store error bodies or exception messages containing credentials.
        outcome["delivery_unknown"] = True
        return
    if payload.get("ok") is False or error:
        outcome["delivery_failed"] = True
        return
    ids = _receipt_ids(payload, name)
    confirmed = False
    if payload.get("ok") is True:
        if name in {"qq_send", "qq_forward_messages", "qq_paste_message"}:
            # Forward acknowledgements can legitimately lack output message IDs;
            # acceptance alone cannot satisfy a scheduled delivery commitment.
            confirmed = payload.get("delivered") is True and bool(ids)
        elif name == "reply_now":
            # Foreground decoded transport confirms its actual body without an
            # ID; receipt-backed task transports may omit that body instead.
            text = payload.get("text")
            confirmed = payload.get("delivered") is True and (
                bool(ids) or (isinstance(text, str) and bool(text.strip())))
        else:
            # Reviewed media adapters return either an explicit delivery flag
            # (including actual caption fallback) or their native data receipt.
            confirmed = payload.get("delivered") is True or bool(ids)
    if confirmed:
        outcome["delivery_confirmed"] = True
        for message_id in ids:
            if message_id not in outcome["delivery_message_ids"]:
                outcome["delivery_message_ids"].append(message_id)
    else:
        # Missing, malformed or merely successful transport output proves no send.
        outcome["delivery_unknown"] = True


def can_finish(outcome: dict) -> bool:
    """Allow quiet tasks or fully confirmed attempts, never mixed partial success."""
    if outcome.get("delivery_attempted") is False:
        return True
    return (outcome.get("delivery_attempted") is True
            and outcome.get("delivery_confirmed") is True
            and outcome.get("delivery_failed") is False
            and outcome.get("delivery_unknown") is False)


def completion_failure(outcome: dict) -> str | None:
    """Return safe stable failure codes, prioritizing ambiguity to avoid retries."""
    if outcome.get("delivery_unknown") is True:
        return "task_delivery_unknown"
    if outcome.get("delivery_failed") is True:
        return "task_delivery_failed"
    return None
