"""Describe top-level message attachments as source data, without I/O or inference.

Only explicit image/mface reference fields can become image references. Cards and
unknown payloads remain untrusted JSON data; they are never searched for images,
interpreted as instructions, or traversed as forwarded messages.
"""

import json
import math
import re
from collections.abc import Mapping
from urllib.parse import urlsplit


# Text, mentions and quoted/forwarded sources have existing dedicated handlers.
_EXCLUDED = frozenset({"text", "plain", "at", "reply", "forward", "forwardmsg",
                       "multimsg", "node", "nodes"})
_KNOWN = frozenset({"face", "mface", "image", "record", "audio", "video", "file",
                    "json", "xml", "card", "cards", "lightapp", "dice", "rps",
                    "poke", "contact", "location"})
# Attribute-only framework objects need the same fields as raw OneBot dictionaries.
_FIELDS = ("id", "summary", "name", "url", "file", "path", "imageURL", "image_url",
           "emoji_id", "emoji_package_id", "key", "file_name", "size", "duration",
           "magic", "sub_type", "cache", "proxy", "timeout", "data", "content",
           "title", "qq", "user_id", "lat", "lon", "latitude", "longitude")
_OMIT = object()


def _get(value, key, default=None):
    """Read supplied mapping/attribute metadata without propagating bad getters."""
    try:
        return value.get(key, default) if isinstance(value, Mapping) else getattr(value, key, default)
    except Exception:
        # Third-party descriptors may raise; unsupported evidence is not invented.
        return default


def _kind(value):
    """Normalize explicit enum/string types without guessing from object names."""
    kind = _get(value, "type")
    if kind is None:
        return ""
    original = kind
    kind = _get(original, "value", original)
    if not isinstance(kind, str):
        kind = _get(original, "name")
    if not isinstance(kind, str):
        return ""
    return kind.rsplit(".", 1)[-1].lower()


def _primitive(value, ancestors=frozenset()):
    """Copy JSON primitives losslessly, omitting cycles and non-JSON objects.

    No arbitrary text, item-count or depth truncation is applied. The source's
    finite JSON tree bounds the result; malformed cyclic Python objects cannot
    expand forever or expose their repr/implementation details.
    """
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else _OMIT
    if not isinstance(value, (Mapping, list, tuple)) or id(value) in ancestors:
        return _OMIT
    ancestors = ancestors | {id(value)}
    if isinstance(value, Mapping):
        result = {}
        for key, child in value.items():
            if not isinstance(key, str):
                continue
            clean = _primitive(child, ancestors)
            if clean is not _OMIT:
                result[key] = clean
        return result
    result = []
    for child in value:
        clean = _primitive(child, ancestors)
        if clean is not _OMIT:
            result.append(clean)
    return result


def _top_segments(value):
    """Unwrap only a top-level event/message array, never nested component data."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError, RecursionError):
            return []
    if isinstance(value, Mapping) and not _kind(value):
        value = value.get("message")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError, RecursionError):
                return []
    if isinstance(value, (list, tuple)):
        return value
    # A real standalone component is safe; event sender/raw text is not evidence.
    return [value] if _kind(value) else []


def _describe(component):
    """Keep explicit source metadata and distinguish unknown attachment types."""
    kind = _kind(component)
    if not kind or kind in _EXCLUDED:
        return None
    if isinstance(component, Mapping):
        supplied = dict(component)
    else:
        supplied = dict(_get(component, "__dict__", {}) or {})
        for key in _FIELDS:
            value = _get(component, key, _OMIT)
            if value is not _OMIT:
                supplied.setdefault(key, value)
    supplied.pop("type", None)
    clean = _primitive(supplied)
    result = {"type": kind}
    if kind in _KNOWN and isinstance(clean.get("data"), Mapping):
        # OneBot stores fields under data, whereas canonical components use attrs.
        # Inner `type` (e.g. contact subtype) stays data, never replaces source type.
        nested = clean.pop("data")
        result.update({key: value for key, value in nested.items() if key != "type"})
        if "type" in nested:
            result["data"] = {"type": nested["type"]}
    result.update(clean)
    return result


def _image_ref(value, field):
    """Validate an explicit image source syntactically, without fetching/stat calls."""
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() in ("http", "https"):
            return value if parsed.hostname and not any(char.isspace() for char in value) else None
        if parsed.scheme.lower() == "data":
            return value if re.match(r"^data:image/[^;,\s]+(?:;[^,\s]*)?,.+$", value, re.DOTALL) else None
        if parsed.scheme.lower() == "base64":
            return value if value.startswith("base64://") and len(value) > 9 else None
        if parsed.scheme.lower() == "file":
            return value if parsed.path else None
    except ValueError:
        return None
    # Only actual file/path fields establish a local source. A face id or an
    # arbitrary string in url/imageURL must not become an invented image URL.
    if field not in ("file", "path"):
        return None
    if parsed.scheme and not re.match(r"^[a-zA-Z]:[\\/]", value):
        return None
    if "://" in value:
        return None
    return value if ("/" in value or "\\" in value or re.search(r"\.[A-Za-z0-9]+$", value)) else None


def describe_event(event):
    """Return recognized/segments/image_refs for current top-level source evidence.

    Raw OneBot arrays are authoritative for each type they actually contain;
    canonical-only types supplement them. This avoids cross-representation
    duplicates while preserving two identical real face/image nodes. Identity,
    wake-up policy, source text, forwarded content and the event remain untouched.
    Unexpected framework failures return the explicit unrecognized empty result.
    """
    empty = {"recognized": False, "segments": [], "image_refs": []}
    try:
        message = _get(event, "message_obj")
        canonical = _get(message, "message")
        if canonical is None:
            getter = _get(event, "get_messages")
            try:
                canonical = getter() if callable(getter) else []
            except Exception:
                # A broken convenience API cannot erase intact raw event evidence.
                canonical = []
        raw = _top_segments(_get(message, "raw_message"))
        canonical = _top_segments(canonical)
        raw_types = {_kind(component) for component in raw if _kind(component)}
        # Raw order/multiplicity is source truth, not a global deduplication set.
        components = list(raw) + [component for component in canonical
                                  if _kind(component) not in raw_types]
        segments = []
        for component in components:
            try:
                described = _describe(component)
            except Exception:
                # One malformed component must not erase another proven image.
                continue
            if described is not None:
                segments.append(described)
        refs = []
        for segment in segments:
            if segment["type"] not in ("image", "mface"):
                continue
            for field in ("url", "imageURL", "image_url", "file", "path"):
                ref = _image_ref(segment.get(field), field)
                if ref:
                    if ref not in refs:
                        refs.append(ref)
                    # Multiple fields commonly identify the same source image.
                    break
        return {"recognized": bool(segments), "segments": segments, "image_refs": refs}
    except Exception:
        return empty
