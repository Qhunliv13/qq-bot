"""Parse and resolve forwarded chats through the event's existing OneBot connection.

This module is framework independent: parsing never fetches, and resolution never
uses a separate HTTP endpoint or invents message text/sender identities.
"""

import inspect
import json
import xml.etree.ElementTree as ET
from collections.abc import Mapping


_MEDIA_TYPES = frozenset({"image", "video", "record", "audio", "file"})


def _get(value, key, default=None):
    """Read either OneBot mappings or AstrBot component attributes uniformly."""
    return value.get(key, default) if isinstance(value, Mapping) else getattr(value, key, default)


def _type(value):
    """Normalize real enum names/values as well as serialized component types."""
    kind = _get(value, "type")
    if kind is None:
        kind = value.__class__.__name__
    kind = getattr(kind, "name", kind)
    return str(kind).rsplit(".", 1)[-1].lower().replace("_", "")


def _data(value):
    """Read component data without assuming AstrBot stores it as a dictionary."""
    data = _get(value, "data")
    return data if isinstance(data, Mapping) else {}


def _first(value, keys):
    """Keep the first present value, including numeric sender identifiers."""
    for key in keys:
        result = _get(value, key)
        if result is not None and result != "":
            return result
    return None


def _sender(node):
    """Preserve supplied identity metadata; unknown senders remain explicitly null."""
    data = _data(node)
    sender = _get(node, "sender") or data.get("sender") or {}
    uid = _first(sender, ("user_id", "id", "uin", "qq"))
    name = _first(sender, ("card", "nickname", "display_name", "name"))
    if uid is None:
        uid = _first(data, ("user_id", "uin", "qq"))
    if uid is None:
        uid = _first(node, ("user_id", "uin", "qq"))
    if name is None:
        name = _first(data, ("nickname", "name"))
    if name is None:
        name = _first(node, ("nickname", "name"))
    return {"id": uid, "display_name": name}


def _card_descriptors(raw):
    """Extract resid/m_resid only after a card proves it is a multimsg card."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            try:
                root = ET.fromstring(raw)
            except (ET.ParseError, ValueError):
                return []
            # viewMultiMsg is the XML discriminator; arbitrary XML ids are unsafe.
            actual = any(str(element.attrib.get("action", "")).lower() == "viewmultimsg"
                         or element.tag.rsplit("}", 1)[-1].lower() == "multimsg"
                         for element in root.iter())
            if not actual:
                return []
            ids = [element.attrib[key] for element in root.iter()
                   for key in ("resid", "m_resid") if element.attrib.get(key)]
            return [{"kind": "forward", "id": str(fid)} for fid in dict.fromkeys(ids)] or [
                {"kind": "forward", "id": None}]
    if not isinstance(raw, Mapping):
        return []
    # Inspect discriminators, not arbitrary text/title fields mentioning multimsg.
    actual = str(raw.get("app", "")).lower() == "com.tencent.multimsg" or str(
        raw.get("view", "")).lower() == "multimsg"
    if not actual:
        return []
    stack, ids, seen = [raw], [], set()
    while stack:
        value = stack.pop()
        if isinstance(value, (Mapping, list, tuple)):
            if id(value) in seen:
                continue
            seen.add(id(value))
        if isinstance(value, Mapping):
            for key, child in value.items():
                if key in ("resid", "m_resid") and child not in (None, ""):
                    ids.append(str(child))
                elif isinstance(child, (Mapping, list, tuple)):
                    stack.append(child)
        elif isinstance(value, (list, tuple)):
            stack.extend(reversed(value))
    return [{"kind": "forward", "id": fid} for fid in dict.fromkeys(ids)] or [
        {"kind": "forward", "id": None}]


def parse_forward_descriptors(value):
    """Return ordered node/forward descriptors without API calls or input mutation.

    Node descriptors contain their original content and normalized sender. Only
    forward-shaped components/cards produce fetchable IDs; ordinary message ids
    and ordinary text/media are deliberately ignored.
    """
    stack, descriptors, seen = [value], [], set()
    while stack:
        current = stack.pop()
        if current is None:
            continue
        if not isinstance(current, (str, int, float, bool)):
            if id(current) in seen:
                continue
            seen.add(id(current))
        if isinstance(current, (list, tuple)):
            stack.extend(reversed(current))
            continue
        kind, data = _type(current), _data(current)
        if kind in ("node", "nodes") or (isinstance(current, Mapping)
                and any(key in current for key in ("sender", "user_id", "uin", "nickname"))
                and ("content" in current or "message" in current)):
            content = _first(data, ("content", "message", "nodes"))
            if content is None:
                content = _first(current, ("content", "message", "nodes"))
            # AstrBot Nodes is a container, not a fabricated sender/message.
            if kind == "nodes":
                stack.append(content)
            else:
                descriptors.append({"kind": "node", "sender": _sender(current),
                                    "content": content, "raw": current})
        elif kind in ("forward", "forwardmsg", "multimsg"):
            fid = _first(data, ("id", "resid", "m_resid"))
            if fid is None:
                fid = _first(current, ("id", "resid", "m_resid"))
            content = _first(data, ("content", "messages", "nodes"))
            if content is None:
                content = _first(current, ("content", "messages", "nodes"))
            if content:
                stack.append(content)
            else:
                descriptors.append({"kind": "forward", "id": str(fid) if fid is not None else None})
        elif kind in ("json", "xml", "card", "lightapp"):
            raw = _first(data, ("data", "content"))
            if raw is None:
                raw = _first(current, ("data", "content"))
            descriptors.extend(_card_descriptors(raw))
        elif isinstance(current, Mapping):
            # Raw event/get_msg envelopes are containers, never inferred forwards.
            for key in ("message", "messages", "raw_message"):
                if key in current:
                    stack.append(current[key])
        elif isinstance(current, str) and current.lstrip().startswith(("{", "<")):
            descriptors.extend(_card_descriptors(current))
    return descriptors


def _segments(content):
    """Flatten node content into ordered text/media/forward parts, guarding cycles."""
    stack, parts, ancestors = [(content, frozenset())], [], set()
    while stack:
        value, ancestors = stack.pop()
        if value is None:
            continue
        if not isinstance(value, (str, int, float, bool)):
            if id(value) in ancestors:
                parts.append(("error", "cyclic_embedded_nodes"))
                continue
            ancestors = ancestors | {id(value)}
        if isinstance(value, (list, tuple)):
            stack.extend((child, ancestors) for child in reversed(value))
            continue
        if isinstance(value, str):
            parts.append(("text", value))
            continue
        kind, data = _type(value), _data(value)
        if kind in ("plain", "text"):
            text = _get(value, "text", data.get("text"))
            if text is not None:
                parts.append(("text", str(text)))
        elif kind in _MEDIA_TYPES:
            metadata = dict(data)
            for key in ("url", "file", "name", "file_name", "size", "duration"):
                supplied = _get(value, key)
                if supplied is not None:
                    metadata.setdefault(key, supplied)
            parts.append(("media", {"type": kind, **metadata}))
        elif kind == "at":
            target = _first(data, ("qq", "id")) or _first(value, ("qq", "id"))
            if target is not None:
                parts.append(("text", "@" + str(target)))
        else:
            for descriptor in parse_forward_descriptors(value):
                parts.append(("descriptor", descriptor))
    return parts


def _unwrap(response):
    """Reject failed OneBot envelopes before exposing their data as messages."""
    if isinstance(response, Mapping):
        code = response.get("retcode", 0)
        if str(code) not in ("0", "None") or response.get("status") in ("failed", "error"):
            return None, "api_error: " + str(response.get("message") or response.get("wording") or code)
        if "data" in response:
            return response["data"], None
    return response, None


def _event_descriptors(event):
    """Prefer parsed components, falling back to the original raw event dictionary."""
    message = _get(event, "message_obj")
    descriptors = parse_forward_descriptors(_get(message, "message"))
    if not descriptors or any(item["kind"] == "forward" and not item.get("id")
                              for item in descriptors):
        raw = parse_forward_descriptors(_get(message, "raw_message"))
        if raw:
            # Raw IDs can recover lossy components without an unnecessary get_msg.
            descriptors = [item for item in descriptors if item["kind"] != "forward"
                           or item.get("id")] + raw
    return descriptors


async def resolve(event, *, forward_id=None, message_id=None):
    """Resolve real forwarded content; report unavailable/empty/error distinctly.

    Explicit message_id opts into get_msg. Implicit current-message lookup occurs
    only for an already recognized forward lacking an ID, never ordinary chat.
    Repeated resource IDs are fetched once; ancestry guards stop recursive cycles.
    """
    descriptors = ([{"kind": "forward", "id": str(forward_id)}]
                   if forward_id is not None else _event_descriptors(event))
    result = {"ok": False, "recognized": bool(descriptors) or message_id is not None,
              "items": [], "text": "", "error": None, "source": "event",
              "errors": []}
    api = _get(_get(_get(event, "bot"), "api"), "call_action")
    cache = {}

    async def call(action, **parameters):
        """Use only the live framework connection and surface transport failures."""
        if not callable(api):
            return None, "api_unavailable"
        try:
            response = api(action, **parameters)
            if inspect.isawaitable(response):
                response = await response
            return _unwrap(response)
        except Exception as exc:
            return None, "api_exception: " + str(exc)

    lookup = message_id
    missing_id = any(item["kind"] == "forward" and not item.get("id") for item in descriptors)
    if lookup is None and missing_id:
        lookup = _get(event, "msg_id") or _get(_get(event, "message_obj"), "message_id")
        if lookup is None:
            getter = _get(event, "get_message_id")
            if callable(getter):
                lookup = getter()
    if lookup is not None and (message_id is not None or missing_id):
        payload, error = await call("get_msg", message_id=lookup)
        result["source"] = "get_msg"
        if error:
            result["errors"].append(error)
        else:
            recovered = parse_forward_descriptors(payload)
            if recovered:
                # Replace unresolved placeholders, but keep embedded source nodes.
                descriptors = [item for item in descriptors if item["kind"] != "forward"
                               or item.get("id")] + recovered
                result["recognized"] = True
            elif message_id is not None and not descriptors:
                result["recognized"] = False

    async def expand(descriptor, active_ids, active_nodes):
        """Render nodes in source order, recursively expanding only genuine forwards."""
        if descriptor["kind"] == "forward":
            fid = descriptor.get("id")
            if not fid:
                result["errors"].append("forward_id_unavailable")
                return
            if fid in active_ids:
                result["errors"].append("cyclic_forward: " + fid)
                return
            if fid not in cache:
                payload, error = await call("get_forward_msg", id=fid)
                result["source"] = "get_forward_msg"
                if not error:
                    if isinstance(payload, Mapping):
                        payload = payload.get("messages", payload.get("message", payload.get("content")))
                    if payload is None:
                        error = "forward_unavailable"
                    elif payload == []:
                        error = "empty_forward"
                cache[fid] = (payload, error)
            payload, error = cache[fid]
            if error:
                result["errors"].append(error)
                return
            nested = parse_forward_descriptors(payload)
            if not nested:
                result["errors"].append("empty_forward")
            for child in nested:
                await expand(child, active_ids | {fid}, active_nodes)
            return
        raw = descriptor.get("raw")
        if id(raw) in active_nodes:
            result["errors"].append("cyclic_embedded_nodes")
            return
        active_nodes = active_nodes | {id(raw)}
        sender, text, media = descriptor["sender"], [], []

        def flush():
            """Emit only supplied content; empty nodes are not invented user messages."""
            if any(text) or media:
                result["items"].append({"sender": dict(sender), "text": "".join(text),
                                        "media": list(media)})
                text.clear()
                media.clear()

        for kind, value in _segments(descriptor.get("content")):
            if kind == "descriptor":
                flush()
                await expand(value, active_ids, active_nodes)
            elif kind == "error":
                result["errors"].append(value)
            elif kind == "text":
                text.append(value)
            else:
                media.append(value)
        flush()

    for descriptor in descriptors:
        await expand(descriptor, frozenset(), frozenset())
    lines = []
    for item in result["items"]:
        identity = item["sender"]["display_name"]
        if identity is None:
            identity = item["sender"]["id"]
        # Media labels attest only attachment presence, never unseen pixel content.
        body = item["text"] + "".join("[" + media["type"] + "]" for media in item["media"])
        lines.append((str(identity) + ": " if identity is not None else "") + body)
    result["text"] = "\n".join(lines)
    result["ok"] = bool(result["items"]) and not result["errors"]
    if result["errors"]:
        result["errors"] = list(dict.fromkeys(result["errors"]))
        result["error"] = result["errors"][0]
    elif result["recognized"] and not result["items"]:
        result["error"] = "empty_forward"
    elif not result["recognized"]:
        result["error"] = "not_forward"
    return result
