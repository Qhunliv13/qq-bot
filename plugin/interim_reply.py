"""Model-chosen public replies for an active tool loop, without transport policy.

The caller supplies the actual current batch and its existing decoded sender.
Only confirmed transport output may enter the caller's per-turn delivery ledger;
this module neither retries unknown outcomes nor schedules mandatory messages.
"""

from collections.abc import Awaitable, Callable, Iterable
import json

from .reply_selection import decode_reply


def schema() -> list[dict]:
    """Offer one optional public-reply action without phases or canned wording."""
    return [{"type": "function", "function": {
        "name": "reply_now",
        "description": "向当前会话发送模型自行决定的公开回复；默认 finish=false，发送后工具回路继续，可多次调用。仅当面向用户的回复已完整结束时可显式设 finish=true：确认实际发送成功后，完成当前批次所有工具与记录，再停止后续模型生成。无需每轮发送，不含固定话术。只发送面向用户的内容，非思考过程。",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "text": {"type": "string"},
                "reply_to": {"type": "array", "items": {"type": "string"}},
                "finish": {"type": "boolean", "default": False}},
            "required": ["text"]}}}]


async def deliver(
    args: dict,
    records: list,
    send: Callable[[str, str | None], Awaitable[str | None]],
    *, allowed_quote_ids=(),
) -> dict:
    """Validate once and send through the parent's existing decoded transport.

    Omitted attribution selects the last real current record, never an invented
    message. Explicit targets are string IDs belonging to the current batch.
    A leading text quote uses current IDs or caller-authorized same-scope archive
    IDs only; it never changes attribution or invents a current human input.
    The callback must preserve normal permission/cleaning policy and accept
    already-decoded text (``decoded=True`` in the parent). Its returned nonempty
    string is the actual delivered body; None or an exception confirms nothing.
    """
    # Reject schema violations before parsing or calling any side-effecting code.
    if (not isinstance(args, dict) or set(args) - {"text", "reply_to", "finish"}
            or not isinstance(args.get("text"), str)
            or ("finish" in args and not isinstance(args["finish"], bool))):
        return {"ok": False, "delivered": False, "error": "invalid_arguments"}
    if "reply_to" in args and (
            not isinstance(args["reply_to"], list)
            or any(not isinstance(target, str) for target in args["reply_to"])):
        return {"ok": False, "delivered": False, "error": "invalid_reply_targets"}

    # Keep empty/no-batch attribution explicit. A malformed last record fails
    # closed inside the shared decoder rather than falling back to another ID.
    targets = args.get("reply_to", [])
    if "reply_to" not in args and records:
        last = records[-1]
        targets = [last.get("id") if isinstance(last, dict) else None]
    decoded = decode_reply(
        json.dumps({"text": args["text"], "reply_to": targets}, ensure_ascii=False),
        records, allow_unattributed=not records, allowed_quote_ids=allowed_quote_ids,
    )
    if decoded["error"]:
        return {"ok": False, "delivered": False, "error": decoded["error"]}
    if not decoded["text"].strip():
        # Silence is an intentional no-op, not a successful transport delivery.
        return {"ok": True, "delivered": False}

    try:
        actual = await send(decoded["text"], decoded["quote_id"])
    except Exception as exc:
        # A transport may have sent before raising. Never retry or leak its
        # exception message (which may contain credentials or private payloads).
        return {"ok": False, "delivered": False, "error": "send_exception",
                "exception_type": type(exc).__name__}
    if not isinstance(actual, str) or not actual.strip():
        return {"ok": False, "delivered": False, "error": "delivery_not_confirmed"}
    result = {"ok": True, "delivered": True, "text": actual,
              "quote_id": decoded["quote_id"]}
    # Only confirmed nonempty delivery can carry this explicit completion signal;
    # omit the default to preserve the established reply result contract.
    if args.get("finish") is True:
        result["finish"] = True
    return result


def already_delivered(final: str, delivered_bodies: Iterable[str]) -> bool:
    """Match only a decoded final body already confirmed in this turn's ledger.

    Strip edge whitespace only: substrings, semantic similarity, and previous
    turns must not suppress a new answer. The parent owns the ledger lifetime.
    """
    if not isinstance(final, str) or not final.strip():
        return False
    return any(isinstance(body, str) and final.strip() == body.strip()
               for body in delivered_bodies)
