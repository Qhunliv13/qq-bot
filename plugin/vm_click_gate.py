"""Turn-local, fail-closed shared screenshot checkpoint for VM clicks and paths.

Only the caller can assert that a model actually saw the returned preview image.
A click ticket is not user authorization, and pixel comparison cannot prevent a
physical screen change between the last screenshot and the eventual VM click.
"""

import asyncio
import hashlib
import io
import math
import os
from pathlib import Path
import secrets
import stat
import time
import uuid

from PIL import Image, ImageDraw, UnidentifiedImageError

from .vm_execution_lease import ExecutionLease
from .vm_pointer_runtime import canonical_pointer_action

# Limit bytes before decoding and pixels before rendering to bound untrusted VM PNGs.
MAX_SCREEN_BYTES = 16 * 1024 * 1024
MAX_SCREEN_PIXELS = 16 * 1024 * 1024
DEFAULT_TTL_SECONDS = 60


def _same_text(actual, expected):
    """Compare exact text including Unicode paths without compare_digest's ASCII trap.

    Surrogate preservation makes malformed model text a non-match rather than an
    uncaught encoding exception; no normalization can alias a different ticket.
    """
    return secrets.compare_digest(actual.encode('utf-8', errors='surrogatepass'),
                                  expected.encode('utf-8', errors='surrogatepass'))


class ClickGate:
    """Hold exactly one kind-bound click or path checkpoint in one plugin model turn."""

    def __init__(self, images_root, ttl_seconds=DEFAULT_TTL_SECONDS):
        """Bind a gate to a trusted images directory, never a shared process-global ticket."""
        self.images_root = Path(images_root)
        # NaN makes every expiry comparison false, and infinity prevents expiry;
        # neither may silently turn a freshness ticket into a permanent grant.
        if (not isinstance(ttl_seconds, (int, float)) or isinstance(ttl_seconds, bool)
                or ttl_seconds <= 0 or not math.isfinite(ttl_seconds)):
            raise ValueError("ttl_seconds must be positive and finite")
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

    @staticmethod
    def _canonical(kind, action):
        """Use the runtime's sole path schema, retaining exact legacy click integers."""
        if kind == "path":
            return canonical_pointer_action(action)
        x, y = action
        if type(x) is not int or type(y) is not int:
            raise ValueError("invalid_preview_arguments")
        return (x, y)

    @staticmethod
    def _fits(kind, action, picture):
        """Bind path coordinates to exactly the captured pixel space, never scale them."""
        if kind == "path":
            return (action.width, action.height) == picture.size
        x, y = action
        return 0 <= x < picture.width and 0 <= y < picture.height

    @staticmethod
    def _render(picture, kind, action, label):
        """Paint a copy only; full path geometry and ordered times never authorize input."""
        marker = picture.copy()
        draw = ImageDraw.Draw(marker)
        if kind == "click":
            x, y = action
            draw.ellipse((x - 12, y - 12, x + 12, y + 12), outline="red", width=3)
            draw.line((x - 16, y, x + 16, y), fill="yellow", width=2)
            draw.line((x, y - 16, x, y + 16), fill="yellow", width=2)
            draw.text((min(x + 18, picture.width - 1), max(0, y - 12)), label[:80], fill="red")
        else:
            draw.line([(p.x, p.y) for p in action.points], fill="red", width=3)
            for index, point in enumerate(action.points):
                # Index + scheduled offset gives direction even for crossing/held paths.
                draw.ellipse((point.x - 2, point.y - 2, point.x + 2, point.y + 2), fill="yellow")
                draw.text((point.x + 4, max(0, point.y - 12)),
                          f"{index}:{point.t_ms}ms", fill="red")
            first, last = action.points[0], action.points[-1]
            draw.ellipse((first.x - 7, first.y - 7, first.x + 7, first.y + 7), outline="green", width=3)
            draw.rectangle((last.x - 7, last.y - 7, last.x + 7, last.y + 7), outline="blue", width=3)
            # Bounded text legend also survives vertex overlap and tiny screenshots
            # in the structured response; image dimensions never change to fit it.
            draw.text((0, 0), "START green -> END blue; index:ms", fill="blue")
        return marker

    async def preview(self, x, y, label, screenshot_callable):
        """Keep the public click API as an adapter to the shared single-slot gate."""
        return await self._preview("click", (x, y), label, screenshot_callable)

    async def preview_path(self, action, label, screenshot_callable):
        """Bind a complete immutable canonical path to the same observed-image gate."""
        return await self._preview("path", action, label, screenshot_callable)

    async def _preview(self, kind, action, label, screenshot_callable):
        """Revoke any kind of old ticket, capture/render safely and store one checkpoint."""
        async with self._lock:
            self._pending = None
            self._observed = False
            try:
                canonical = self._canonical(kind, action)
            except ValueError:
                return {"ok": False, "error": "invalid_preview_arguments"}
            if not isinstance(label, str):
                return {"ok": False, "error": "invalid_preview_arguments"}
            if not callable(screenshot_callable):
                return {"ok": False, "error": "screenshot_unavailable"}
            try:
                picture, digest = self._image(await screenshot_callable())
                if not self._fits(kind, canonical, picture):
                    return {"ok": False, "error": "coordinates_out_of_bounds"}
                marker = self._render(picture, kind, canonical, label)
                output = io.BytesIO()
                marker.save(output, format="PNG")
                data = output.getvalue()
                if len(data) > MAX_SCREEN_BYTES:
                    raise ValueError("marker PNG exceeds byte limit")
                # O_EXCL prevents replacing any file; random basename stays in images_root.
                path = self._root() / f"pre-{uuid.uuid4().hex}.png"
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                fd = os.open(path, flags, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                ticket = secrets.token_urlsafe(32)
                x, y = canonical if kind == "click" else (None, None)
                # Preserve legacy tuple positions; kind and immutable action bind both APIs.
                self._pending = (ticket, x, y, digest, time.monotonic() + self.ttl_seconds,
                                 str(path), kind, canonical)
                result = {"ok": True, "ticket": ticket, "screen_sha256": digest,
                          "path": str(path), "width": picture.width, "height": picture.height,
                          "message": "Inspect the marked screenshot before explicitly confirming."}
                if kind == "click":
                    result.update(x=x, y=y)
                else:
                    # This detached wire copy is descriptive, never the stored authority.
                    result["action"] = dict(version=canonical.version, button=canonical.button,
                        width=canonical.width, height=canonical.height,
                        points=[dict(x=p.x, y=p.y, t_ms=p.t_ms) for p in canonical.points])
                    result["legend"] = [f"{i}: ({p.x},{p.y}) at {p.t_ms}ms"
                                        for i, p in enumerate(canonical.points)]
                return result
            except Exception:
                # Untrusted paths, corrupt pixels and storage diagnostics stay private.
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
            if not _same_text(path, self._pending[5]):
                return False
            if time.monotonic() >= self._pending[4]:
                self._pending = None
                return False
            self._observed = True
            return True

    async def confirm(self, ticket, screenshot_callable, click_callable, *, x, y,
                      screen_sha256, viewed):
        """Keep legacy click arguments and receipts on the common confirmation path."""
        return await self._confirm("click", ticket, screenshot_callable, click_callable,
                                   (x, y), screen_sha256, viewed)

    async def confirm_path(self, ticket, screenshot_callable, path_callable, *, action,
                           screen_sha256, viewed, authorized_path_callable=None):
        """Optionally hand trusted executors a lease without changing legacy callbacks."""
        return await self._confirm("path", ticket, screenshot_callable, path_callable,
                                   action, screen_sha256, viewed, authorized_path_callable)

    async def _confirm(self, kind, ticket, screenshot_callable, execute_callable,
                       action, screen_sha256, viewed, authorized_path_callable=None):
        """Consume/recheck once under the same lock for clicks and full pointer paths."""
        async with self._lock:
            pending = self._pending
            # Consume even on failure: a rejected or ambiguous attempt needs a fresh preview.
            observed = self._observed
            self._pending = None
            self._observed = False
            if pending is None:
                return {"ok": False, "error": "no_pending_preview"}
            expected_ticket, _x, _y, expected_digest, expires, _path, expected_kind, stored = pending
            try:
                canonical = self._canonical(kind, action)
            except ValueError:
                return {"ok": False, "error": "preview_confirmation_mismatch"}
            if (kind != expected_kind or canonical != stored
                    or not isinstance(ticket, str) or not _same_text(ticket, expected_ticket)
                    or not isinstance(screen_sha256, str)
                    or not _same_text(screen_sha256, expected_digest)
                    or viewed is not True):
                return {"ok": False, "error": "preview_confirmation_mismatch"}
            if not observed:
                return {"ok": False, "error": "preview_image_not_seen_by_model"}
            if time.monotonic() >= expires:
                return {"ok": False, "error": "preview_expired"}
            # Opt-in is explicit, never a TypeError retry or model-controlled flag.
            executor = (authorized_path_callable if authorized_path_callable is not None
                        else execute_callable)
            if not callable(screenshot_callable) or not callable(executor):
                return {"ok": False, "error": "backend_unavailable"}
            try:
                picture, digest = self._image(await screenshot_callable())
            except Exception:
                # Backend exceptions may contain private paths, credentials or
                # screen data. Preserve the fixed outcome without echoing them.
                return {"ok": False, "error": "screenshot_unavailable"}
            # Capturing/decoding can itself span the deadline. Recheck after
            # that await so an old checkpoint never authorizes a delayed click.
            if time.monotonic() >= expires:
                return {"ok": False, "error": "preview_expired"}
            if digest != expected_digest or not self._fits(kind, stored, picture):
                return {"ok": False, "error": "screen_changed"}
            uncertain = f"{kind}_outcome_uncertain"
            try:
                # Pass the stored record, never mutable confirmation arguments.
                if authorized_path_callable is not None:
                    # Capture the original deadline and its clock, not a renewed TTL.
                    lease = ExecutionLease(expires, time.monotonic)
                    receipt = await executor(stored, lease=lease)
                else:
                    receipt = await executor(*stored) if kind == "click" else await executor(stored)
            except Exception:
                # Uncertain execution consumes the ticket and never exposes exception text.
                return {"ok": False, "error": uncertain}
            if not isinstance(receipt, dict) or receipt.get("ok") is not True:
                result = {"ok": False, "error": uncertain}
                if kind == "click":
                    result["receipt"] = receipt  # Preserve the public legacy click envelope.
                return result
            if kind == "click":
                return {"ok": True, "x": stored[0], "y": stored[1], "receipt": receipt}
            return {"ok": True, "receipt": receipt}
