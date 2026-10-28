"""Explicit, detached fact metadata; descriptive labels never grant authority.

Legacy entries remain unspecified: neither text, categories, provenance nor an
identity-like subject is evidence of kind, permissions or ownership. New kinds
must be added to this single vocabulary deliberately, not inferred by heuristics.
"""

from __future__ import annotations

# ``unspecified`` is essential for lossless legacy projection and explicit resets.
FACT_KINDS = ("unspecified", "fact", "preference", "instruction", "note")
# Superseded entries remain stored and retrievable; status does not delete facts.
FACT_STATUSES = ("active", "superseded")


def structure_schema() -> dict:
    """Expose the same explicit vocabulary to model tools without duplicate enums."""
    return {"type": "object", "properties": {
        "kind": {"type": "string", "enum": list(FACT_KINDS)},
        "status": {"type": "string", "enum": list(FACT_STATUSES)},
        "subject": {"anyOf": [{"type": "null"}, {"type": "object", "properties": {
            "id": {"type": "string", "minLength": 1},
            "display_name": {"type": "string", "minLength": 1}},
            "required": ["id"], "additionalProperties": False}]}},
        "additionalProperties": False}


def normalize_structure(value=None) -> dict:
    """Validate explicit metadata and return a fresh normalized value without I/O.

    None describes a legacy/unspecified entry. Callers decide whether None means
    preserve existing storage; this function never mutates or interprets content.
    Only the documented fields are accepted, avoiding silent authority injection.
    """
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise TypeError("structure must be an object")
    if set(value) - {"kind", "subject", "status"}:
        raise ValueError("structure only accepts kind, subject and status")
    kind = value.get("kind", "unspecified")
    status = value.get("status", "active")
    if not isinstance(kind, str) or kind not in FACT_KINDS:
        raise ValueError("invalid structure kind")
    if not isinstance(status, str) or status not in FACT_STATUSES:
        raise ValueError("invalid structure status")
    subject = value.get("subject")
    if subject is not None:
        if not isinstance(subject, dict):
            raise TypeError("structure subject must be an object or null")
        if set(subject) - {"id", "display_name"}:
            raise ValueError("subject only accepts id and display_name")
        identity = subject.get("id")
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError("subject id must be a nonempty string")
        # Copy only explicit fields; no ID comes from a name, source or text.
        normalized_subject = {"id": identity.strip()}
        if "display_name" in subject:
            name = subject["display_name"]
            if not isinstance(name, str) or not name.strip():
                raise ValueError("subject display_name must be a nonempty string")
            normalized_subject["display_name"] = name.strip()
        subject = normalized_subject
    return {"kind": kind, "status": status, "subject": subject}
