"""Pure visual-reference provenance; a publisher is not the depicted image owner.

Keep source occurrences distinct from visual deduplication and transport limits.
No fetching, permissions, persona rules, role inference, or event mutation belong
here. Existing message adapters remain authoritative for participant identities.
"""

from .message_batch import (
    _data, _field, _identity, _kind, _quote, message_record, message_segments,
)


def _image_ref(component) -> str:
    """Project existing image fields in their established url/file/path priority."""
    data = _data(component)
    for name in ("url", "file", "path"):
        value = _field(data, name)
        if value:
            return str(value)
    return ""


def collect_sources(event) -> list[dict]:
    """Collect explicit images and their actual message publishers without I/O.

    A quoted source's message_id identifies the quoted message; the separate
    containing_message_id identifies the current message carrying that reference.
    Unknown quoted/forwarded identities remain unknown, never the current sender.
    Duplicate representations of one (ref, source) collapse, while the same ref
    published by different sources retains every association. Neither reference
    strings nor source text are interpreted as evidence of depicted ownership.
    """
    current = message_record(event, "")
    current_id = current["id"]
    current_sender = dict(current["sender"])
    records = []

    def append(ref, source):
        """Deduplicate equivalent metadata paths without erasing source relations."""
        if not ref:
            return
        record = {"ref": str(ref), "source": source}
        if record not in records:
            records.append(record)

    def current_source():
        """Return fresh publisher metadata so records do not share mutable fields."""
        return {"message_id": current_id, "sender": dict(current_sender),
                "relation": "current"}

    for component in message_segments(event):
        kind = _kind(component)
        if kind == "image":
            append(_image_ref(component), current_source())
        elif kind in ("reply", "quote"):
            # Reuse the quote adapter rather than maintaining another identity parser.
            quote = _quote([component])
            if quote is None:
                continue
            data = _data(component)
            chain = _field(data, "chain") or _field(data, "message") or ()
            # Quote prose is not a segment stream and cannot supply image references.
            if isinstance(chain, str):
                continue
            for child in chain:
                if _kind(child) == "image":
                    append(_image_ref(child), {
                        "message_id": quote["id"],
                        "sender": dict(quote["sender"]), "relation": "quoted",
                        "quoted_message_id": quote["id"],
                        "containing_message_id": current_id,
                    })

    forwarded = _field(event, "_higan_forward_context", {}) or {}
    for item in _field(forwarded, "items", ()) or ():
        # The forward reader supplies normalized node sender metadata. Its containing
        # event ID is not a substitute for an unavailable forwarded message ID.
        sender = _identity(_field(item, "sender"))
        for media in _field(item, "media", ()) or ():
            if _field(media, "type") != "image":
                continue
            ref = _field(media, "url") or _field(media, "file") or ""
            append(ref, {"message_id": str(_field(item, "message_id") or ""),
                         "sender": dict(sender), "relation": "forwarded",
                         "containing_message_id": current_id})

    nontext = _field(event, "_higan_nontext_context", {}) or {}
    for ref in _field(nontext, "image_refs", ()) or ():
        # This existing reader guarantees current top-level sources, excluding Reply.
        append(ref, current_source())
    return records
