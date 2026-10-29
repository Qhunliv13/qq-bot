"""Pure final-output routing, independent of wording and semantic similarity.

Ordinary foreground replies retain the existing implicit publication contract.
Background deliberation and a continuation after public output use the existing
explicit delivery disposition; an unmarked completion remains a draft.
"""

from .reply_selection import decode_reply


def has_public_delivery(turn, scope):
    """Detect an actual same-scope send, never infer one from model prose."""
    if turn.get("_interim_replies"):
        return True
    return any(
        isinstance(item, dict) and isinstance(item.get("delivery"), dict)
        and item["delivery"].get("status") in ("native_confirmed", "observed_send_return", "local_recorded")
        and item["delivery"].get("destination_scope") == str(scope)
        for item in turn.get("_deliveries", []))


def select(raw, records, *, explicit=False, allow_unattributed=False):
    """Decode once and distinguish a public operation from an unmarked draft.

    This does not end tool work, compare messages, infer completion, invent a
    receipt or restrict the number of public actions. An explicit delivery=new
    remains available even for identical text or the same incoming message.
    """
    selection = decode_reply(raw, records, allow_unattributed=allow_unattributed)
    publish = (not selection["error"] and bool(selection["text"])
               and (not explicit or selection.get("delivery") == "new"))
    return {"publish": publish, "selection": selection,
            "draft": raw if explicit and not publish and isinstance(raw, str) else None}
