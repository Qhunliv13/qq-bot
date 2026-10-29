"""Fail closed on unstructured final model output without inspecting prose keywords.

This boundary is pure except for its one trusted, caller-provided async repair
callback. Tool-bearing intermediate turns are never interpreted or replayed here.
"""

import json
from typing import Any, Awaitable, Callable

from .reply_selection import decode_reply, is_silence


# The host supplies the original transcript without repeating the private draft.
# This instruction controls only the final envelope, not its persona or actions.
FINAL_RESPONSE_CONTRACT = (
    "Final output only: a JSON object with reply_to (actual current message IDs "
    "only), text (public reply body only), and optional delivery ('new' or "
    "'already_delivered'). Do not put internal planning or drafts in public "
    "text. Do not fabricate IDs, send receipts, or tool results. No tools. "
    "For no reply, output empty text or SILENT."
)


class FinalResponseUnavailable(Exception):
    """Signal a failed one-shot finalization without exposing private model text."""

    code = "final_response_unavailable"

    def __str__(self) -> str:
        """Keep even stringified errors independent of private drafts or failures."""
        return self.code


def publishable(raw: str, records: Any, *, allow_unattributed: bool = True) -> bool:
    """Require an explicit object envelope and reuse shared target/delivery validation.

    A JSON string, array, malformed object, or legacy plain reply is not an
    envelope. Parsing here checks eligibility only; decode_reply owns all schema
    and current-target validation. Empty raw is handled by the caller as silence.
    """
    if not isinstance(raw, str):
        return False
    try:
        candidate = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return False
    if not (isinstance(candidate, dict) and "reply_to" in candidate and "text" in candidate):
        return False
    selection = decode_reply(raw, records, allow_unattributed=allow_unattributed)
    return selection["structured"] and selection["error"] is None


async def ensure_final_response(
    raw: str,
    calls: Any,
    records: Any,
    repair: Callable[[str], Awaitable[tuple[str, Any]]],
    *,
    allow_unattributed: bool = True,
) -> tuple[str, Any]:
    """Return a validated final envelope or silence; try trusted repair once.

    The host owns transcript construction and must prevent tools in repair. Never
    interpret intermediate tool rounds as public output, never replay calls, and
    never return an invalid private draft as a fallback.
    """
    if calls:
        return raw, calls
    if is_silence(raw):
        return raw, calls
    # A structured target/delivery error is an authorization failure, never a
    # draft to be rewritten into a newly attributed reply by another model call.
    try:
        structured = isinstance(json.loads(raw), dict)
    except (ValueError, TypeError):
        structured = False
    if structured:
        selection = decode_reply(raw, records, allow_unattributed=allow_unattributed)
        if selection["error"]:
            raise FinalResponseUnavailable()
    if publishable(raw, records, allow_unattributed=allow_unattributed):
        # Structured empty text or an explicit SILENT body is a silent outcome,
        # not a JSON string to be sent literally after the tool loop returns.
        if not decode_reply(raw, records, allow_unattributed=allow_unattributed)["text"]:
            return "", calls
        return raw, calls
    try:
        fixed_raw, fixed_calls = await repair(raw)
    except Exception:
        # Exception context/message can contain the draft: suppress chaining.
        raise FinalResponseUnavailable() from None
    if fixed_calls or not (is_silence(fixed_raw) or publishable(
        fixed_raw, records, allow_unattributed=allow_unattributed
    )):
        raise FinalResponseUnavailable()
    if not is_silence(fixed_raw) and not decode_reply(
        fixed_raw, records, allow_unattributed=allow_unattributed
    )["text"]:
        return "", fixed_calls
    return fixed_raw, fixed_calls
