"""Bind model memory interpretations to actual current source messages, never role claims."""
from copy import deepcopy


def memory_source(scope, messages, selected_ids=None):
    """Select only caller-supplied native records; unknown selections fail before writing.

    A quote is retained inside its parent record, not promoted into a message sent
    by the quoting author. Text and kind remain model interpretations, not proven
    assertions by every participant whose message happened to share a queue batch.
    """
    if not isinstance(messages, list) or any(not isinstance(row, dict) for row in messages):
        raise ValueError("invalid_source_messages")
    if selected_ids is None:
        selected = messages
        selection = "current_batch_context"
    else:
        if (not isinstance(selected_ids, list) or not selected_ids
                or any(not isinstance(mid, str) or not mid.strip() for mid in selected_ids)):
            raise ValueError("source_message_ids_must_be_nonempty_strings")
        ids = list(dict.fromkeys(selected_ids))
        available = {str(row.get("id")) for row in messages
                     if row.get("id") is not None and str(row.get("id"))}
        if any(mid not in available for mid in ids):
            raise ValueError("source_message_not_in_current_batch")
        selected = [row for row in messages if str(row.get("id")) in ids]
        selection = "explicit_current_message_ids"
    frozen = deepcopy(selected)
    # Source records cannot self-assert system/administrator permissions via fields.
    for row in frozen:
        if isinstance(row.get("sender"), dict):
            row["sender"].pop("role", None)
    return {"name": "model_saved_memory", "trust": "untrusted_data", "scope": str(scope),
            "attribution": "model_interpretation_of_source_messages", "selection": selection,
            "message_ids": [str(row["id"]) for row in frozen if row.get("id") is not None],
            "messages": frozen}
