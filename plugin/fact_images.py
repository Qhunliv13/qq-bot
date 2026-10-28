"""Validate persistent fact images without fetching URLs or decoding image pixels."""

from __future__ import annotations

import base64
import binascii
import re

# Match the existing caller's 4 MiB image conversion threshold, without a new count cap.
MAX_IMAGE_BYTES = 4 * 1024 * 1024
_DATA_IMAGE = re.compile(
    r"data:image/([A-Za-z0-9][A-Za-z0-9!#$&^_.+\-]*);base64,([A-Za-z0-9+/]*={0,2})",
    re.IGNORECASE,
)


def _image_mime(payload: bytes) -> str:
    """Identify supported binary signatures; never trust a caller's MIME label alone."""
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("images must contain PNG, JPEG, GIF or WebP binary data")


def normalize_images(images) -> list[str]:
    """Return detached canonical data URLs, rejecting paths, URLs and invalid payloads.

    MIME spelling is normalized from binary signatures because existing converters
    may label downloaded PNG images as JPEG. No network or pixel decoder is used.
    """
    if images is None:
        return []
    if not isinstance(images, list):
        raise TypeError("images must be an array of data-image strings")
    result = []
    seen = set()
    for ref in images:
        if not isinstance(ref, str):
            raise TypeError("images must contain only data-image strings")
        match = _DATA_IMAGE.fullmatch(ref)
        if match is None or match.group(1).lower() in {"octet", "octet-stream", "html"}:
            raise ValueError("images must use base64 image data URLs")
        encoded = match.group(2)
        # Bound allocation before decoding, then enforce the exact binary threshold.
        if len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
            raise ValueError("each image must be at most 4 MiB")
        try:
            payload = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("images must contain valid base64") from exc
        if not payload or len(payload) > MAX_IMAGE_BYTES:
            raise ValueError("each image must be nonempty and at most 4 MiB")
        mime = _image_mime(payload)
        canonical = "data:" + mime + ";base64," + base64.b64encode(payload).decode("ascii")
        if canonical not in seen:
            seen.add(canonical)
            result.append(canonical)
    return result


def images_metadata(images) -> list[dict]:
    """Return fresh zero-based metadata only, never inline bytes or mutable storage."""
    result = []
    for index, ref in enumerate(normalize_images(images)):
        header, encoded = ref.split(",", 1)
        padding = len(encoded) - len(encoded.rstrip("="))
        result.append({"index": index, "mime": header[5:-7],
                       "bytes": len(encoded) // 4 * 3 - padding})
    return result
