"""Decode explicit current-batch selections without guessing intent from prose.

This pure boundary performs no model calls, delivery, scheduling, or I/O. Legacy
quote controls share one lexical parser with the native transport.
"""

import json
import re
from typing import Any


# Reserve only complete raw leading controls; malformed IDs must not leak into
# outgoing prose, while examples inside quotes, fences, or sentences stay literal.
_LEGACY_QUOTE_PREFIX = re.compile(r"\s*\[quote:([^\r\n\[\]]*)\]")
# Native QQ IDs use positive ASCII decimal syntax, never Unicode digits or hex.
_NATIVE_QUOTE_ID = re.compile(r"[0-9]{2,20}")


def extract_legacy_quote(text: str, *, allowed_ids=None) -> tuple[str, str | None]:
    """Consume raw leading quote controls and return cleaned text and a native ID.

    Repeated leading ``[quote:...]`` controls are reserved transport syntax, even
    when their ID is malformed or unauthorized. Select only the first positive
    ASCII decimal ID of 2–20 digits permitted by ``allowed_ids``. ``None`` keeps
    legacy historical-ID compatibility; an empty collection permits no target.
    Callers supply authoritative conversation-scoped IDs when validation is
    needed; this helper never extracts IDs from history prose or guesses a target.
    Quoted, fenced, inline, and incomplete markers are literal content. No English
    or Chinese keyword heuristics, NLP, model calls, or other I/O are involved.
    Whitespace following the last consumed marker remains part of the body.
    """
    allowed = None if allowed_ids is None else {str(value) for value in allowed_ids}
    offset = 0
    quote_id = None
    while True:
        marker = _LEGACY_QUOTE_PREFIX.match(text, offset)
        if marker is None:
            break
        candidate = marker.group(1)
        # Invalid prefixes are removed without substituting any current record;
        # later authorized prefixes can still provide the first usable target.
        if (quote_id is None and _NATIVE_QUOTE_ID.fullmatch(candidate)
                and int(candidate) > 0
                and (allowed is None or candidate in allowed)):
            quote_id = candidate
        offset = marker.end()
    return text[offset:], quote_id


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
    Plain prose never implies a selection. Its leading legacy controls are
    removed, and a native decimal quote may still refer to historical messages.
    The transport remains responsible for conversation-scoped authorization.
    Optional ``delivery`` is exactly ``new`` or ``already_delivered``. The latter
    marks a draft as internal rather than a new public reply; validated targets
    remain metadata, but text and quote side effects are suppressed. It does not
    assert delivery success or create a receipt. Omitted delivery preserves the
    legacy five-key result contract and all existing routing behavior.
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
        delivery = payload.get("delivery", "new")
        # Only explicit enum strings control disposition; malformed values must
        # fail closed, never become visible JSON or implicitly publish a draft.
        if not isinstance(delivery, str) or delivery not in ("new", "already_delivered"):
            result.update(text="", error="invalid_reply_delivery")
            return result
        if "delivery" in payload:
            # Keep omitted-field compatibility, including callers comparing the
            # complete historical five-key result, rather than adding a default.
            result["delivery"] = delivery
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
            if records and delivery != "already_delivered":
                valid = valid and (bool(selected) or not reply_text.strip())
        if not valid:
            result.update(text="", error="invalid_reply_targets")
            return result
        # A model-selected internal draft never enters public transport. This is
        # not a success receipt: only the caller's real tool results prove sends.
        # Validate every supplied target before permitting this disposition.
        if delivery == "already_delivered":
            result.update(text="", reply_to=selected)
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
        reply_text, _ = extract_legacy_quote(reply_text, allowed_ids=())
        result.update(text=reply_text if reply_text.strip() else "", reply_to=selected,
                      quote_id=quote_id)
        return result

    # Historical IDs remain compatible; the transport can validate their scope.
    # Do not rerun silence or JSON parsing after removing transport controls.
    result["text"], result["quote_id"] = extract_legacy_quote(text)
    return result
