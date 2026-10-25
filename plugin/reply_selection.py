"""Decode explicit current-batch selections without guessing intent from prose.

This pure boundary performs no model calls, delivery, scheduling, or I/O. Legacy
text remains intact so the existing transport can handle its optional quote tag.
"""

import json
import re
from typing import Any


# Keep the existing platform quote transport's decimal-ID syntax unchanged.
_LEGACY_QUOTE = re.compile(r"\[quote:(\d{2,20})\]")


def _message_id(value: Any) -> str | None:
    """Normalize only actual string/integer identifiers, never arbitrary objects."""
    # bool is an int subclass, but it is not a message identifier.
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    result = str(value)
    return result if result else None


def decode_reply(raw: str | None, records: Any) -> dict:
    """Return text, reply_to, quote_id, structured, and error for one final reply.

    Only a JSON object with explicit ``reply_to`` and ``text`` keys enters the
    structured protocol, and only when a current message batch exists. A task
    with no current records retains legacy text, including JSON-shaped content.
    Structured targets must all name actual current record IDs; invalid output
    fails closed rather than selecting a fabricated target or rerunning a model.
    Plain prose never implies a selection. Its explicit legacy quote may refer
    to history and is left in the original text for the existing transport.
    """
    text = "" if raw is None else raw
    result = {"text": text, "reply_to": [], "quote_id": None,
              "structured": False, "error": None}
    # Whitespace and the exact SILENT sentinel are the two legacy silence forms.
    if not text.strip() or text.strip() == "SILENT":
        result["text"] = ""
        return result

    # Parsing alone must not reinterpret ordinary JSON answers as reply schemas.
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        payload = None
    if records and isinstance(payload, dict) and "reply_to" in payload and "text" in payload:
        result["structured"] = True
        targets = payload["reply_to"]
        reply_text = payload["text"]
        current_ids = {_message_id(record.get("id")) for record in records
                       if isinstance(record, dict)}
        current_ids.discard(None)
        valid = isinstance(targets, list) and isinstance(reply_text, str)
        selected = []
        if valid:
            # Validate the whole list before accepting any of it; partial repair
            # would falsely attribute the model's reply to a different subset.
            for target in targets:
                message_id = _message_id(target)
                if message_id is None or message_id not in current_ids:
                    valid = False
                    break
                if message_id not in selected:
                    selected.append(message_id)
            # Nonempty prose without explicit current attribution is not valid.
            valid = valid and (bool(selected) or not reply_text.strip())
        if not valid:
            result.update(text="", error="invalid_reply_targets")
            return result
        last_id = _message_id(records[-1].get("id")) if records and isinstance(records[-1], dict) else None
        quote_id = next((message_id for message_id in selected if message_id != last_id), None)
        result.update(text=reply_text if reply_text.strip() else "", reply_to=selected,
                      quote_id=quote_id)
        return result

    # Legacy quotes deliberately are not validated against the current batch:
    # historical message references were already supported by the transport.
    quote = _LEGACY_QUOTE.search(text)
    if quote:
        result["quote_id"] = quote.group(1)
    return result
