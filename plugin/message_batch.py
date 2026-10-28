"""Pure queued-message records; attribution comes from segment metadata, not prose.

No AstrBot imports or I/O belong here: the caller retains delivery, image fetching,
and policy enforcement while this module preserves every queued utterance.
"""

from dataclasses import dataclass
import json
import math
import re
from typing import Any


@dataclass
class PendingMessage:
    """Name the existing five queue fields without changing their tuple order."""

    event: Any
    text: str
    has_image: bool
    woken: bool
    history: Any

    def as_tuple(self) -> tuple:
        """Expose the legacy queue representation for callers migrating incrementally."""
        return self.event, self.text, self.has_image, self.woken, self.history

    def __iter__(self):
        """Allow legacy five-value unpacking without a second queue implementation."""
        return iter(self.as_tuple())


def _field(obj, name, default=None):
    """Read dict and framework object metadata through the same pure adapter."""
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def _call(event, name):
    """Treat an unavailable framework getter as missing, retaining metadata fallbacks."""
    getter = getattr(event, name, None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:
        # Partial/offline framework events can still carry usable message_obj data.
        return None


def _kind(segment) -> str:
    """Recognize framework enum, plain string, and lightweight component classes."""
    kind = _field(segment, "type", "")
    value = getattr(kind, "value", kind)
    if not isinstance(value, str):
        value = getattr(kind, "name", "")
    return str(value or type(segment).__name__).lower().split(".")[-1]


def message_segments(event) -> list:
    """Read only the top-level canonical stream, never descend into quoted chains."""
    segments = _call(event, "get_messages")
    if not segments:
        segments = _field(_field(event, "message_obj"), "message", ())
    return list(segments or ())


def _data(segment):
    """Unwrap OneBot data dictionaries while keeping native components unchanged."""
    return _field(segment, "data", {}) if isinstance(segment, dict) else segment


def _identity(sender, sender_id="", display_name="") -> dict:
    """Keep numeric identity distinct from user-controlled names and role-like prose."""
    ident = _field(sender, "user_id") or _field(sender, "id") or sender_id
    name = (_field(sender, "display_name") or _field(sender, "card")
            or _field(sender, "nickname") or _field(sender, "name") or display_name)
    return {"id": str(ident or ""), "display_name": str(name or "")}


def _mentions(segments) -> list[dict]:
    """Collect actual top-level mentions, retaining rendering names as metadata only."""
    mentions = []
    for segment in segments:
        if _kind(segment) != "at":
            continue
        data = _data(segment)
        target = _field(data, "qq") or _field(data, "target") or _field(data, "user_id")
        if target is None or str(target) == "":
            continue
        name = (_field(data, "display_name") or _field(data, "name")
                or _field(data, "nickname") or "")
        mentions.append({"id": str(target), "display_name": str(name)})
    return mentions


def at_targets(event) -> list[str]:
    """Expose unique actual mention IDs so admission and batching can share parsing."""
    return list(dict.fromkeys(item["id"] for item in _mentions(message_segments(event))))


def _plain_text(segments) -> str:
    """Recover quote prose from plain/text segments, never stringify nested metadata."""
    parts = []
    for segment in segments or ():
        if _kind(segment) in ("plain", "text"):
            parts.append(str(_field(_data(segment), "text", "") or ""))
    return "".join(parts)


def _quote(segments):
    """Keep the first quoted source separate from the actual sender and wake targets."""
    for segment in segments:
        if _kind(segment) not in ("reply", "quote"):
            continue
        data = _data(segment)
        body = (_field(data, "message_str") or _field(data, "text")
                or _field(data, "body") or "")
        if not body:
            chain = _field(data, "chain") or _field(data, "message") or ()
            body = chain if isinstance(chain, str) else _plain_text(chain)
        sender = _identity(_field(data, "sender"), _field(data, "sender_id", ""),
                           _field(data, "sender_nickname", ""))
        return {"id": str(_field(data, "id") or _field(data, "message_id") or ""),
                "sender": sender, "body": str(body)}
    return None


def _body(event, text, segments, mentions) -> str:
    """Separate rendered Ats from prose without erasing literal Plain content.

    The framework string can render a qq-only At as ``@nickname(id)``. Recover
    its name only when the actual target ID and non-Plain rendered occurrence
    prove the association. A matching token in Plain remains user-authored text.
    """
    body = str(text or "")
    raw = _call(event, "get_message_str")
    has_plain = any(_kind(segment) in ("plain", "text") for segment in segments)
    plain = _plain_text(segments)
    for mention in mentions:
        if not mention["display_name"] and isinstance(raw, str):
            # Bound parsing to one line and a normal nickname length; never infer IDs.
            pattern = r"@([^@\r\n]{1,128}?)\(" + re.escape(mention["id"]) + r"\)"
            for match in re.finditer(pattern, raw):
                token = match.group(0)
                if raw.count(token) > plain.count(token):
                    mention["display_name"] = match.group(1)
                    break
    if has_plain and isinstance(raw, str) and body == raw:
        # Only a byte-for-byte original framework string can be reconstructed;
        # forwarded/appended context supplied by the caller must not disappear.
        return plain.strip()
    for mention in mentions:
        if mention["display_name"]:
            rendered = "@%s(%s)" % (mention["display_name"], mention["id"])
            if body.count(rendered) > plain.count(rendered):
                # Retain literal Plain occurrences even when an At has the same name.
                body = body.replace(rendered, "", 1)
    return body.strip()


def _native_evidence(obj, segments) -> dict:
    """Extract additive native evidence only; pure parsing performs no I/O.

    Never inspect quoted chains, interpret prose, or retain the full raw payload:
    cards and private URLs are not needed to identify this message's forwards.
    """
    raw = _field(obj, "raw_message")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, RecursionError):
            # Ordinary message text and malformed JSON provide no native metadata.
            raw = None
    raw = raw if isinstance(raw, dict) else {}
    evidence = {}
    candidates = ((raw.get("time"), "onebot.time"),
                  (_field(obj, "timestamp"), "message_obj.timestamp"),
                  (_field(obj, "time"), "message_obj.time"))
    for value, source in candidates:
        # bool is an int subclass; integers are finite without float conversion.
        if type(value) is int or (type(value) is float and math.isfinite(value)):
            evidence.update(native_time=value, time_source=source)
            break
    forward_ids, seen = [], set()
    native_segments = raw.get("message")
    # Native and canonical streams can mirror each other; deduplicate only here.
    for segment in list(native_segments if isinstance(native_segments, list) else ()) + segments:
        if _kind(segment) != "forward":
            continue
        ident = _field(_data(segment), "id")
        if not ((isinstance(ident, str) and ident) or type(ident) is int):
            continue
        key = (type(ident), ident)
        if key not in seen:
            seen.add(key)
            forward_ids.append(ident)
    if forward_ids:
        evidence["forward_ids"] = forward_ids
    return evidence


def message_record(event, text: str, who: str = "") -> dict:
    """Build an unambiguous record, removing only metadata-proven rendered mentions.

    A matching display name alone never authorizes removal. Each top-level At
    authorizes at most one exact ``@display_name(id)`` token; quoted Ats do not.
    ``who`` is only a fallback display label, never a source of IDs or privileges.
    """
    obj = _field(event, "message_obj")
    sender_id, sender_name = _call(event, "get_sender_id"), _call(event, "get_sender_name")
    sender = _identity(_field(obj, "sender"), sender_id, sender_name or who)
    # Framework getters are authoritative when complete, even with stale metadata.
    if sender_id:
        sender["id"] = str(sender_id)
    if sender_name:
        sender["display_name"] = str(sender_name)
    segments = message_segments(event)
    mentions = _mentions(segments)
    body = _body(event, text, segments, mentions)
    record = {"id": str(_field(obj, "message_id") or _field(event, "message_id") or ""),
              "sender": sender, "body": body, "mentions": mentions,
              "quote": _quote(segments)}
    # Keep the exact legacy shape when no native evidence is actually available.
    record.update(_native_evidence(obj, segments))
    return record


# Keep the originally requested spelling as an alias, not a duplicate implementation.
make_record = message_record


def collect_batch(entries, own_id: str = "") -> dict:
    """Collapse every queued tuple into one lossless envelope with a policy-safe anchor.

    Prefer the latest actual own At, then the latest wake, then the latest event.
    Freeze history at the first entry; later history may already include this batch.
    Attachment retrieval stays with the caller, which must inspect all entry events.
    """
    pending = [entry if isinstance(entry, PendingMessage) else PendingMessage(*entry)
               for entry in entries]
    if not pending:
        raise ValueError("Cannot collect an empty message batch")
    records = [message_record(entry.event, entry.text) for entry in pending]
    direct, awake = [], []
    for entry, record in zip(pending, records):
        own = str(own_id or _call(entry.event, "get_self_id")
                  or _field(_field(entry.event, "message_obj"), "self_id", "") or "")
        if own and any(item["id"] == own for item in record["mentions"]):
            direct.append(entry)
        if entry.woken:
            awake.append(entry)
    anchor = (direct or awake or pending)[-1]
    return {"event": anchor.event,
            "text": json.dumps({"messages": records}, ensure_ascii=False),
            "has_image": any(entry.has_image for entry in pending),
            "woken": any(entry.woken for entry in pending),
            "history": pending[0].history, "records": records}
