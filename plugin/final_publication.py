"""Pure final-output routing, independent of wording and semantic similarity.

Ordinary foreground replies retain the existing implicit publication contract.
Background deliberation and a continuation after public output use the existing
explicit delivery disposition; an unmarked completion remains a draft.
"""

from .reply_selection import decode_reply


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
