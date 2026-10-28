"""Convert explicitly selected attachments using the existing visual transport only."""

import asyncio


async def snapshot_images(refs, converter):
    """Resolve every reference before fact mutation; never silently save partial sets.

    The host converter already handles local paths, HTTP and data URLs. Validation
    and canonicalization belong to fact_images, shared by storage and tool calls.
    No input message is automatically promoted to a durable fact or attachment.
    """
    from .fact_images import normalize_images
    if refs is None:
        return None
    if not isinstance(refs, list) or any(not isinstance(ref, str) or not ref.strip() for ref in refs):
        raise ValueError("images must be an array of nonempty image references")
    snapshots = []
    for ref in refs:
        image = await asyncio.to_thread(converter, ref)
        if not image:
            raise ValueError("image_unavailable: no fact was changed")
        snapshots.append(image)
    return normalize_images(snapshots)
