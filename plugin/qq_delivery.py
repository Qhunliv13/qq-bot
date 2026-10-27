"""Build native OneBot sends and confirm delivery without transport or policy defaults.

The caller supplies its native action transport (for example ``_onebot_http``).
A successful HTTP request alone is never evidence that a message was delivered.
"""

from collections.abc import Awaitable, Callable
from typing import Any


def _destination(value: Any, name: str, *, required: bool) -> int:
    """Reject coercion and booleans so no malformed ID silently changes a target."""
    if type(value) is not int or (value <= 0 if required else value != 0):
        raise ValueError(f"{name} must be a positive integer" if required else f"{name} is not valid for this kind")
    return value


def _mention(value: Any) -> str:
    """Accept explicit positive QQ IDs only, never CQ strings or special targets."""
    if type(value) is int and value > 0:
        return str(value)
    # ASCII digits deliberately exclude Unicode lookalikes accepted by isdigit().
    if isinstance(value, str) and value and value.isascii() and value.isdigit() and any(char != "0" for char in value):
        return value
    raise ValueError("mentions must contain positive integer QQ IDs or digit strings")


def build(kind: str, text: str, user_id: int = 0, group_id: int = 0,
          mentions: list[int | str] | None = None) -> tuple[str, dict]:
    """Build one native action with explicit destinations and literal text segments.

    Only group sends support mentions. Temporary private sends require both IDs;
    unrelated destination arguments are rejected instead of being silently ignored.
    Invalid input raises ValueError before any caller can perform network IO.
    """
    if not isinstance(kind, str) or kind not in ("group", "private", "temp"):
        raise ValueError("kind must be group, private, or temp")
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    _destination(user_id, "user_id", required=kind != "group")
    _destination(group_id, "group_id", required=kind != "private")
    if mentions is not None and not isinstance(mentions, list):
        raise ValueError("mentions must be a list")
    if kind != "group" and mentions:
        raise ValueError("mentions are supported only for group sends")
    # Structured mentions cannot be injected by interpreting CQ-looking text.
    message = [{"type": "at", "data": {"qq": _mention(value)}} for value in (mentions or [])]
    message.append({"type": "text", "data": {"text": text}})
    if kind == "group":
        return "send_group_msg", {"group_id": group_id, "message": message}
    params = {"user_id": user_id, "message": message}
    if kind == "temp":
        # OneBot temporary sessions use send_private_msg with their source group.
        params["group_id"] = group_id
    return "send_private_msg", params


def receipt(response: Any, *, action: str) -> dict:
    """Require native success plus a positive message ID; never expose raw responses.

    ``ok`` with null data is unconfirmed, not a successful delivery. Only native
    envelopes are accepted; unwrapped adapter data must not bypass that check.
    """
    envelope = response if isinstance(response, dict) else {}
    status = envelope.get("status")
    retcode = envelope.get("retcode")
    code_valid = type(retcode) is int
    # Keep only protocol metadata; arbitrary upstream strings may contain secrets.
    safe_status = status if isinstance(status, str) and status in ("ok", "failed", "async") else None
    failed = safe_status == "failed" or (code_valid and retcode != 0)
    if safe_status == "ok" and code_valid and retcode == 0:
        data = envelope.get("data")
        message_id = data.get("message_id") if isinstance(data, dict) else None
        try:
            confirmed_id = _mention(message_id)
        except ValueError:
            # A transport acknowledgement without an ID cannot confirm delivery.
            pass
        else:
            return {"ok": True, "delivered": True, "message_id": confirmed_id, "action": action}
    return {
        "ok": False,
        "delivered": False,
        "error": "onebot_action_failed" if failed else "delivery_not_confirmed",
        "action": action,
        "retcode": retcode if code_valid else None,
        "status": safe_status,
    }


async def send(kind: str, text: str, user_id: int = 0, group_id: int = 0,
               mentions: list[int | str] | None = None,
               call_action: Callable[[str, dict], Awaitable[Any]] | None = None) -> dict:
    """Validate then await exactly one native action, without retries or hidden IO.

    Validation errors propagate before transport. Transport exceptions become safe
    failures with only their class name, since their message can leak credentials.
    """
    action, params = build(kind, text, user_id, group_id, mentions)
    if not callable(call_action):
        raise ValueError("call_action must be an async native action callback")
    try:
        response = await call_action(action, params)
    except Exception as exc:
        # Retrying an unknown send outcome risks duplicate externally visible sends.
        return {"ok": False, "delivered": False, "error": "transport_exception", "action": action, "exception": type(exc).__name__}
    return receipt(response, action=action)
