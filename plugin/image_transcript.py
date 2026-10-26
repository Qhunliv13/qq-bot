"""Pure image-message assembly for an append-only native tool transcript."""

from collections.abc import Callable, Sequence


# A neutral data label distinguishes tool-acquired images from a new user request.
IMAGE_OBSERVATION = "Tool image observation (data from the preceding tool results)."


def with_images(messages: Sequence[dict], refs: Sequence,
                converter: Callable[[object], str], limit: int, *,
                initial: bool = False) -> list[dict]:
    """Copy the list, attaching initial refs once or appending new image data.

    The caller owns which references are new and persists the returned list before
    its next request. Existing messages and multipart attachments are never mutated;
    only the initial user dictionary is copied when explicitly initializing it.
    Limits apply to this new batch, never to already submitted image messages.
    """
    result = list(messages)
    if limit <= 0 or not refs:
        # No usable batch must not rewrite string content into a multipart array.
        return result
    user_index = next((index for index, item in enumerate(result)
                       if item.get("role") == "user"), None) if initial else None
    original = result[user_index].get("content") if user_index is not None else None
    parts = list(original) if isinstance(original, list) else [
        {"type": "text", "text": str(original or "")}]
    # Preserve existing attachments and avoid adding an identical converted image.
    urls = {part.get("image_url", {}).get("url") for part in parts
            if isinstance(part, dict) and part.get("type") == "image_url"
            and isinstance(part.get("image_url"), dict)}
    images = []
    seen_refs = set()
    for ref in list(refs)[-limit:]:
        key = str(ref)
        if key in seen_refs:
            # Repeated references in one tool batch are observations of one image.
            continue
        seen_refs.add(key)
        url = converter(ref)
        if url and url not in urls:
            urls.add(url)
            images.append({"type": "image_url", "image_url": {"url": url}})
    if not images:
        return result
    if user_index is not None:
        # Initial enrichment happens before submission; preserve all other fields.
        result[user_index] = dict(result[user_index], content=parts + images)
    else:
        # All assistant/tool pairs are already complete when the host calls us.
        result.append({"role": "user", "content": [
            {"type": "text", "text": IMAGE_OBSERVATION}, *images]})
    return result
