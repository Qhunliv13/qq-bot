"""Decode explicit current-batch selections without guessing intent from prose.

This pure boundary performs no model calls, delivery, scheduling, or I/O. Legacy
text remains intact so the existing transport can handle its optional quote tag.
"""

import json
import re
from typing import Any


# Keep the existing platform quote transport's decimal-ID syntax unchanged.
_LEGACY_QUOTE = re.compile(r"\[quote:(\d{2,20})\]")


def _has_terminal_silence(text: str) -> bool:
    """Reserve a literal final SILENT line outside backtick/tilde code fences.

    Earlier narration is internal when this explicit disposition is supplied.
    Literal examples must be quoted or fenced; an unclosed fence conservatively
    keeps its contents literal. This does not normalize punctuation or wrappers.
    """
    lines = text.splitlines()
    # Whole-token normalization belongs to is_silence; multiline control is a
    # deliberately narrower protocol, never a substring or prose-suffix match.
    if len(lines) < 2 or lines[-1].strip().upper() != "SILENT":
        return False
    fence_char = None
    fence_length = 0
    for line in lines[:-1]:
        marker = re.match(r"^[ \t]*(`{3,}|~{3,})(.*)$", line)
        if marker is None:
            continue
        run, suffix = marker.groups()
        if fence_char is None:
            # Remember the delimiter and length: another fence kind, a shorter
            # run, or a would-be closer with text cannot end this literal block.
            fence_char, fence_length = run[0], len(run)
        elif run[0] == fence_char and len(run) >= fence_length and not suffix.strip():
            fence_char = None
            fence_length = 0
    return fence_char is None


def is_silence(value: Any) -> bool:
    """Recognize empty output, a whole token, or a terminal SILENT disposition.

    Preserve the established case/whitespace and ``。.!！`` edge-punctuation
    normalization for whole output, including one balanced ASCII/fullwidth
    parenthesis pair and one valid JSON-string layer. After that single decode,
    a final unquoted, unfenced standalone literal SILENT line (case-insensitive)
    suppresses the entire completion, including any preceding narration. Use
    quoted/fenced text for literal examples. Multiline control does not inherit
    whole-token punctuation or parenthesis normalization. Never unwrap objects,
    arrays, nested parentheses, or arbitrary quotes. Structured replies must
    validate targets first so silence cannot bypass attribution.
    """
    # Missing output already ends a turn; other nonstrings are not protocol text.
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    text = value.strip()
    # Only JSON string syntax is an accepted wrapper, not a guessed quotation.
    if text.startswith('"'):
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            # Do not unwrap malformed/trailing JSON; a literal final disposition
            # can still follow arbitrary narration that begins with a quote.
            pass
        else:
            # Successful JSON beginning with a quote is necessarily a string;
            # decode once only, never recursively expanding nested serialization.
            text = decoded.strip()
    # Keep empty-output behavior separate: empty parentheses are not a sentinel.
    if not text:
        return True
    token = text.upper().strip("。.!！")
    # Observed model output uses one balanced parenthesis pair, not arbitrary
    # markup. Strip once only so mixed, nested, and unbalanced pairs stay prose.
    if ((token.startswith("(") and token.endswith(")"))
            or (token.startswith("（") and token.endswith("）"))):
        token = token[1:-1].strip().strip("。.!！")
    # Equality and an explicit final-line disposition preserve ordinary mentions;
    # scan the original normalized text, not punctuation-stripped example code.
    return token == "SILENT" or _has_terminal_silence(text)


def _message_id(value: Any) -> str | None:
    """Normalize only actual string/integer identifiers, never arbitrary objects."""
    # bool is an int subclass, but it is not a message identifier.
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    result = str(value)
    return result if result else None


def decode_reply(raw: str | None, records: Any, *, allow_unattributed: bool = False) -> dict:
    """Return text, reply_to, quote_id, structured, and error for one final reply.

    Only a JSON object with explicit ``reply_to`` and ``text`` keys enters the
    structured protocol when a current batch exists or ``allow_unattributed``
    explicitly opts a no-current-record task in. By default, no-current-record
    replies retain legacy text, including JSON-shaped content. Opted-in tasks
    require an empty target list and string text; they cannot fabricate quotes.
    Structured targets must all name actual current record IDs; invalid output
    fails closed rather than selecting a fabricated target or rerunning a model.
    Plain prose never implies a selection. Its explicit legacy quote may refer
    to history and is left in the original text for the existing transport.
    """
    text = "" if raw is None else raw
    result = {"text": text, "reply_to": [], "quote_id": None,
              "structured": False, "error": None}
    # Use the shared explicit-disposition boundary, never inferred prose intent.
    if is_silence(text):
        result["text"] = ""
        return result

    # Parsing alone must not reinterpret ordinary JSON answers as reply schemas.
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        payload = None
    if (records or allow_unattributed) and isinstance(payload, dict) and "reply_to" in payload and "text" in payload:
        result["structured"] = True
        targets = payload["reply_to"]
        reply_text = payload["text"]
        current_ids = {_message_id(record.get("id")) for record in (records or [])
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
            # Current-batch replies still require attribution for nonempty text.
            # Explicit no-batch tasks can speak without it, but the empty ID set
            # above rejects every target rather than guessing a historical one.
            if records:
                valid = valid and (bool(selected) or not reply_text.strip())
        if not valid:
            result.update(text="", error="invalid_reply_targets")
            return result
        # Validate attribution first: a control token cannot legitimize bad IDs.
        if is_silence(reply_text):
            # Preserve validated selection metadata but never manufacture a quote
            # for a completion that must have no delivery or quote side effects.
            result.update(text="", reply_to=selected)
            return result
        last_id = _message_id(records[-1].get("id")) if records and isinstance(records[-1], dict) else None
        quote_id = next((message_id for message_id in selected if message_id != last_id), None)
        # Structured attribution is the single source of truth. An embedded legacy
        # marker must not silently redirect this reply to an unrelated message.
        reply_text = _LEGACY_QUOTE.sub("", reply_text)
        result.update(text=reply_text if reply_text.strip() else "", reply_to=selected,
                      quote_id=quote_id)
        return result

    # Legacy quotes deliberately are not validated against the current batch:
    # historical message references were already supported by the transport.
    quote = _LEGACY_QUOTE.search(text)
    if quote:
        result["quote_id"] = quote.group(1)
    return result
