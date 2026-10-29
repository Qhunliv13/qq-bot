"""Turn-local, fail-closed screenshot checkpoint for VM mouse clicks.

Only the caller can assert that a model actually saw the returned preview image.
A click ticket is not user authorization, and pixel comparison cannot prevent a
physical screen change between the last screenshot and the eventual VM click.
"""

import asyncio
import hashlib
import io
import os
from pathlib import Path
import secrets
import stat
import time
import uuid

from PIL import Image, ImageDraw, UnidentifiedImageError

# Limit bytes before decoding and pixels before rendering to bound untrusted VM PNGs.
MAX_SCREEN_BYTES = 16 * 1024 * 1024
MAX_SCREEN_PIXELS = 16 * 1024 * 1024
DEFAULT_TTL_SECONDS = 60


class ClickGate:
    """Hold exactly one opaque click checkpoint within one plugin model turn."""

    def __init__(self, images_root, ttl_seconds=DEFAULT_TTL_SECONDS):
        """Bind a gate to a trusted images directory, never a shared process-global ticket."""
        self.images_root = Path(images_root)
        if not isinstance(ttl_seconds, (int, float)) or isinstance(ttl_seconds, bool) or ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.ttl_seconds = ttl_seconds
        self._pending = None
        self._observed = False
        # Serialize preview, validation and click so overlapping tool calls cannot reuse a ticket.
        self._lock = asyncio.Lock()

    def _root(self):
        """Reject absent or symlinked image directories before any screenshot access."""
        root = self.images_root
        if not root.is_absolute() or not root.is_dir():
            raise ValueError("unsafe images root")
        # Reject symlinked ancestors too: resolving only the last directory could
        # redirect image reads/writes outside the configured HIGAN_ROOT tree.
        if any(part.is_symlink() for part in (root, *root.parents)):
            raise ValueError("symlink in images root")
        return root.resolve(strict=True)

    def _image(self, screenshot):
        """Read and validate actual local PNG bytes from a direct child of images_root."""
        if not isinstance(screenshot, dict) or screenshot.get("ok") is not True:
            raise ValueError("screenshot failed")
        raw_path = screenshot.get("path")
        if not isinstance(raw_path, (str, Path)):
            raise ValueError("missing screenshot path")
        path = Path(raw_path)
        root = self._root()
        # Do not trust arbitrary absolute paths returned by the VM or a symlink alias.
        if (not path.is_absolute() or path.is_symlink()
                or path.parent != self.images_root or path.parent.resolve(strict=True) != root):
            raise ValueError("screenshot outside images root")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_SCREEN_BYTES:
                raise ValueError("invalid screenshot file size or type")
            data = handle.read(MAX_SCREEN_BYTES + 1)
        if not 0 < len(data) <= MAX_SCREEN_BYTES:
            raise ValueError("screenshot exceeds byte limit")
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.format != "PNG" or image.width <= 0 or image.height <= 0:
                    raise ValueError("screenshot is not a PNG")
                if image.width * image.height > MAX_SCREEN_PIXELS:
                    raise ValueError("screenshot exceeds pixel limit")
                # Loading validates the complete PNG before a token can be issued.
                image.load()
                picture = image.convert("RGB")
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ValueError("invalid screenshot PNG") from exc
        # Digest decoded RGB pixels, including dimensions, not the path, mtime, or metadata.
        digest = hashlib.sha256(
            f"{picture.width}x{picture.height}:".encode() + picture.tobytes()
        ).hexdigest()
        return picture, digest

    async def preview(self, x, y, label, screenshot_callable):
        """Issue a 60-second single-use ticket and immutable visible marker PNG."""
        async with self._lock:
            self._pending = None  # A new checkpoint revokes any earlier one.
            self._observed = False
            if type(x) is not int or type(y) is not int or not isinstance(label, str):
                return {"ok": False, "error": "invalid_preview_arguments"}
            if not callable(screenshot_callable):
                return {"ok": False, "error": "screenshot_unavailable"}
            try:
                picture, digest = self._image(await screenshot_callable())
                if not 0 <= x < picture.width or not 0 <= y < picture.height:
                    return {"ok": False, "error": "coordinates_out_of_bounds"}
                # Paint only a copy; the captured source remains unchanged for verification.
                marker = picture.copy()
                draw = ImageDraw.Draw(marker)
                draw.ellipse((x - 12, y - 12, x + 12, y + 12), outline="red", width=3)
                draw.line((x - 16, y, x + 16, y), fill="yellow", width=2)
                draw.line((x, y - 16, x, y + 16), fill="yellow", width=2)
                draw.text((min(x + 18, picture.width - 1), max(0, y - 12)), label[:80], fill="red")
                output = io.BytesIO()
                marker.save(output, format="PNG")
                data = output.getvalue()
                if len(data) > MAX_SCREEN_BYTES:
                    raise ValueError("marker PNG exceeds byte limit")
                # O_EXCL prevents replacing any file; random basename remains within images_root.
                path = self._root() / f"pre-{uuid.uuid4().hex}.png"
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                fd = os.open(path, flags, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                ticket = secrets.token_urlsafe(32)
                self._pending = (ticket, x, y, digest, time.monotonic() + self.ttl_seconds, str(path))
                return {"ok": True, "ticket": ticket, "x": x, "y": y,
                        "screen_sha256": digest, "path": str(path),
                        "width": picture.width, "height": picture.height,
                        "message": "Inspect the marked screenshot before explicitly confirming."}
            except Exception:
                # Storage failures, untrusted paths and corrupt pixels are
                # implementation diagnostics, not paths to disclose in a model turn.
                return {"ok": False, "error": "preview_unavailable"}

    @property
    def pending_marker_path(self):
        """Expose only this turn's active preview path for visual transport checks."""
        return self._pending[5] if self._pending is not None else None

    async def mark_observed(self, path):
        """Record a completed model request that actually included this marker's pixels."""
        async with self._lock:
            if self._pending is None or not isinstance(path, str):
                return False
            if not secrets.compare_digest(path, self._pending[5]):
                return False
            if time.monotonic() >= self._pending[4]:
                self._pending = None
                return False
            self._observed = True
            return True

    async def confirm(self, ticket, screenshot_callable, click_callable, *, x, y,
                      screen_sha256, viewed):
        """Consume a matching visible checkpoint before any potentially uncertain click."""
        async with self._lock:
            pending = self._pending
            # Consume even on failure: a rejected or ambiguous attempt needs a fresh preview.
            observed = self._observed
            self._pending = None
            self._observed = False
            if pending is None:
                return {"ok": False, "error": "no_pending_preview"}
            expected_ticket, expected_x, expected_y, expected_digest, expires, _path = pending
            if (not isinstance(ticket, str) or not secrets.compare_digest(ticket, expected_ticket)
                    or type(x) is not int or type(y) is not int
                    or (x, y) != (expected_x, expected_y)
                    or not isinstance(screen_sha256, str)
                    or not secrets.compare_digest(screen_sha256, expected_digest)
                    or viewed is not True):
                return {"ok": False, "error": "preview_confirmation_mismatch"}
            if not observed:
                return {"ok": False, "error": "preview_image_not_seen_by_model"}
            if time.monotonic() >= expires:
                return {"ok": False, "error": "preview_expired"}
            if not callable(screenshot_callable) or not callable(click_callable):
                return {"ok": False, "error": "backend_unavailable"}
            try:
                picture, digest = self._image(await screenshot_callable())
            except Exception as exc:
                return {"ok": False, "error": "screenshot_unavailable", "detail": str(exc)}
            # Capturing/decoding can itself span the deadline. Recheck after
            # that await so an old checkpoint never authorizes a delayed click.
            if time.monotonic() >= expires:
                return {"ok": False, "error": "preview_expired"}
            if digest != expected_digest or not 0 <= x < picture.width or not 0 <= y < picture.height:
                return {"ok": False, "error": "screen_changed"}
            try:
                # No optimistic success: failures may still have clicked, and replay is forbidden.
                receipt = await click_callable(x, y)
            except Exception as exc:
                return {"ok": False, "error": "click_outcome_uncertain", "detail": str(exc)}
            if not isinstance(receipt, dict) or receipt.get("ok") is not True:
                return {"ok": False, "error": "click_outcome_uncertain", "receipt": receipt}
            return {"ok": True, "x": x, "y": y, "receipt": receipt}
