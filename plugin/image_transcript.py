"""Pure image-message assembly for an append-only native tool transcript."""

from collections.abc import Callable, Sequence
import json


# A neutral data label distinguishes tool-acquired images from a new user request.
IMAGE_OBSERVATION = "Tool image observation (data from the preceding tool results)."


def with_images(messages: Sequence[dict], refs: Sequence,
                converter: Callable[[object], str], limit: int, *,
                initial: bool = False,
                source_records: Sequence[dict] | None = None) -> list[dict]:
    """Copy the list, attaching initial refs once or appending new image data.

    The caller owns which references are new and persists the returned list before
    its next request. Existing messages and multipart attachments are never mutated;
    only the initial user dictionary is copied when explicitly initializing it.
    Limits apply to this new batch, never to already submitted image messages.
    Optional source records label only newly emitted images as untrusted data;
    their source objects retain caller-provided fields and identifier types. An
    empty or unmatched record list explicitly means unknown origins. Omitting
    records preserves the legacy wire format, including neutral tool observations.
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
    # Only same-batch labels may be enriched; submitted parts must stay frozen.
    origins_by_url = {}
    seen_refs = set()
    for ref in list(refs)[-limit:]:
        key = str(ref)
        if key in seen_refs:
            # Repeated references in one tool batch are observations of one image.
            continue
        seen_refs.add(key)
        url = converter(ref)
        if not url:
            # A failed conversion has no image to which provenance could apply.
            continue
        origins = [record["source"] for record in source_records
                   if str(record["ref"]) == key] if source_records is not None else []
        if url in urls:
            if url in origins_by_url:
                # Distinct refs can resolve to one image; retain every real origin.
                origins_by_url[url][1].extend(origins)
            continue
        urls.add(url)
        if source_records is not None:
            # Serialize after conversion deduplication so merged origins stay adjacent.
            label = {"type": "text", "text": ""}
            origins_by_url[url] = (label, origins)
            images.append(label)
        images.append({"type": "image_url", "image_url": {"url": url}})
    for label, origins in origins_by_url.values():
        # References stay out of metadata: the following image is their sole carrier.
        label["text"] = json.dumps({"source": "attachment_provenance",
                                    "trust": "untrusted_data", "origins": origins},
                                   ensure_ascii=False)
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
