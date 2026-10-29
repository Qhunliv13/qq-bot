"""Offline tests for the turn-local VM screenshot click checkpoint."""

import asyncio
import importlib.util
from pathlib import Path
import tempfile
import unittest

from PIL import Image


_MODULE_PATH = Path(__file__).resolve().parents[1] / "plugin" / "vm_click_gate.py"
_SPEC = importlib.util.spec_from_file_location("vm_click_gate_test_subject", _MODULE_PATH)
gate_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gate_module)
ClickGate = gate_module.ClickGate


class ClickGateTests(unittest.IsolatedAsyncioTestCase):
    """Exercise in-memory tickets with temporary actual PNG screenshots."""

    async def asyncSetUp(self):
        """Create a private VM-style images directory for every test."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "images"
        self.root.mkdir()
        self.shot = self.root / "shot.png"
        self.paint((255, 255, 255))
        self.gate = ClickGate(self.root)
        self.clicks = []

    def paint(self, color, size=(80, 60)):
        """Save a real PNG so tests compare decoded pixels rather than filenames."""
        Image.new("RGB", size, color).save(self.shot)

    async def screenshot(self):
        """Return the backend's mutable screenshot pathname."""
        return {"ok": True, "path": str(self.shot)}

    async def click(self, x, y):
        """Record calls to prove denied confirmations never reach VM input."""
        self.clicks.append((x, y))
        return {"ok": True, "result": "clicked"}

    async def checkpoint(self):
        """Create an ordinary valid preview for subsequent negative cases."""
        preview = await self.gate.preview(20, 25, "Target", self.screenshot)
        self.assertTrue(preview["ok"], preview)
        self.assertTrue(await self.gate.mark_observed(preview["path"]))
        return preview

    async def approve(self, preview, **overrides):
        """Echo the model-visible ticket, digest, location and explicit viewing flag."""
        args = dict(x=preview["x"], y=preview["y"],
                    screen_sha256=preview["screen_sha256"], viewed=True)
        args.update(overrides)
        return await self.gate.confirm(preview["ticket"], self.screenshot, self.click, **args)

    def test_nonfinite_ttl_cannot_disable_screenshot_freshness(self):
        """Reject NaN/infinite TTLs before any screenshot or click is possible."""
        for value in (float('nan'), float('inf'), float('-inf'), 0, -1, True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    ClickGate(self.root, ttl_seconds=value)
        self.assertEqual(ClickGate(self.root, ttl_seconds=0.5).ttl_seconds, 0.5)
        self.assertEqual(self.clicks, [])

    async def test_confirmation_before_visible_model_request_fails_closed(self):
        """A preview URL alone cannot substitute for pixels sent to the model."""
        preview = await self.gate.preview(20, 25, "Target", self.screenshot)
        self.assertTrue(preview["ok"])
        result = await self.approve(preview)
        self.assertEqual(result["error"], "preview_image_not_seen_by_model")
        self.assertEqual(self.clicks, [])
        self.assertFalse(await self.gate.mark_observed(preview["path"]))

    async def test_valid_preview_and_one_time_confirm(self):
        """Return a distinct immutable marker and consume the ticket once."""
        preview = await self.checkpoint()
        self.assertEqual((preview["width"], preview["height"]), (80, 60))
        marker = Path(preview["path"])
        self.assertEqual(marker.parent, self.root)
        self.assertTrue(marker.name.startswith("pre-"))
        self.assertNotEqual(marker.read_bytes(), self.shot.read_bytes())
        self.assertTrue((await self.approve(preview))["ok"])
        self.assertEqual((await self.approve(preview))["error"], "no_pending_preview")
        self.assertEqual(self.clicks, [(20, 25)])

    async def test_same_path_different_pixels_rejected(self):
        """Mutable screenshot filenames cannot authorize a changed desktop."""
        preview = await self.checkpoint()
        self.paint((254, 255, 255))
        self.assertEqual((await self.approve(preview))["error"], "screen_changed")
        self.assertEqual(self.clicks, [])

    async def test_ticket_digest_coordinates_and_viewing_required(self):
        """Reject each missing or false model-visible confirmation component."""
        for override in ({"x": 21}, {"x": True}, {"y": False},
                         {"screen_sha256": "wrong"}, {"viewed": False},
                         {"viewed": 1}):
            with self.subTest(override=override):
                preview = await self.checkpoint()
                self.assertEqual((await self.approve(preview, **override))["error"],
                                 "preview_confirmation_mismatch")
        preview = await self.checkpoint()
        self.assertEqual((await self.gate.confirm("wrong", self.screenshot, self.click,
                        x=20, y=25, screen_sha256=preview["screen_sha256"],
                        viewed=True))["error"], "preview_confirmation_mismatch")
        self.assertEqual(self.clicks, [])

    async def test_turn_and_repreview_invalidate_tickets(self):
        """Never accept a token from another gate or a revoked preview."""
        old = await self.checkpoint()
        other = ClickGate(self.root)
        rejected = await other.confirm(old["ticket"], self.screenshot, self.click,
                                       x=20, y=25, screen_sha256=old["screen_sha256"], viewed=True)
        self.assertEqual(rejected["error"], "no_pending_preview")
        new = await self.checkpoint()
        self.assertNotEqual(old["ticket"], new["ticket"])
        self.assertEqual((await self.approve(old))["error"], "preview_confirmation_mismatch")
        self.assertEqual(self.clicks, [])

    async def test_expiry_and_no_replay(self):
        """Expire by monotonic time and burn the ticket on rejection."""
        from unittest.mock import patch
        with patch.object(gate_module.time, "monotonic", return_value=100):
            preview = await self.checkpoint()
        with patch.object(gate_module.time, "monotonic", return_value=161):
            self.assertEqual((await self.approve(preview))["error"], "preview_expired")
        self.assertEqual((await self.approve(preview))["error"], "no_pending_preview")
        self.assertEqual(self.clicks, [])

    async def test_capture_crossing_deadline_cannot_execute_click(self):
        """A fresh screen capture is insufficient when the ticket expires during it."""
        from unittest.mock import patch
        with patch.object(gate_module.time, "monotonic", return_value=100):
            preview = await self.checkpoint()
        clock = {"now": 150}

        async def delayed_screen():
            """Complete capture only after the previously valid deadline passes."""
            clock["now"] = 161
            return await self.screenshot()

        with patch.object(gate_module.time, "monotonic", side_effect=lambda: clock["now"]):
            result = await self.gate.confirm(preview["ticket"], delayed_screen, self.click,
                x=20, y=25, screen_sha256=preview["screen_sha256"], viewed=True)
        self.assertEqual(result["error"], "preview_expired")
        self.assertEqual(self.clicks, [])
        self.assertEqual((await self.approve(preview))["error"], "no_pending_preview")

    async def test_bad_png_bounds_and_fail_closed_screenshots(self):
        """Neither malformed images nor failed backend captures create tickets."""
        for x, y in ((-1, 0), (80, 0), (0, 60), (True, 0), (0, False)):
            with self.subTest(x=x, y=y):
                result = await self.gate.preview(x, y, "target", self.screenshot)
                self.assertFalse(result["ok"])
        self.shot.write_bytes(b"not-a-png")
        self.assertFalse((await self.gate.preview(1, 1, "target", self.screenshot))["ok"])
        self.paint((255, 255, 255))
        preview = await self.checkpoint()

        async def failed():
            """Simulate a backend that returned no valid new screenshot."""
            return {"ok": False, "error": "offline"}

        result = await self.gate.confirm(preview["ticket"], failed, self.click,
                                         x=20, y=25,
                                         screen_sha256=preview["screen_sha256"], viewed=True)
        self.assertEqual(result["error"], "screenshot_unavailable")
        self.assertEqual(self.clicks, [])

    async def test_symlinks_traversal_and_oversized_png(self):
        """Never read another directory or follow a screenshot symlink."""
        outside = Path(self.temp.name) / "outside.png"
        Image.new("RGB", (80, 60), "white").save(outside)

        async def other():
            """Return a valid PNG outside the trusted images directory."""
            return {"ok": True, "path": str(outside)}

        self.assertFalse((await self.gate.preview(20, 25, "T", other))["ok"])
        alias = self.root / "alias.png"
        try:
            alias.symlink_to(outside)
        except (OSError, NotImplementedError):
            pass  # Unprivileged Windows agents cannot create symlinks.
        else:
            async def symlink():
                """Return a symlink disguised as a direct child of images."""
                return {"ok": True, "path": str(alias)}
            self.assertFalse((await self.gate.preview(20, 25, "T", symlink))["ok"])
        self.shot.write_bytes(b"0" * (gate_module.MAX_SCREEN_BYTES + 1))
        self.assertFalse((await self.gate.preview(20, 25, "T", self.screenshot))["ok"])

    async def test_permission_denial_and_capture_exceptions_fail_closed(self):
        """Never click after screenshot read failures, permission errors, or exceptions."""
        from unittest.mock import patch
        preview = await self.checkpoint()
        original_open = gate_module.os.open

        def deny_shot(path, flags, *args):
            """Simulate image permission denial without changing preview marker writes."""
            if Path(path) == self.shot:
                raise PermissionError("screen inaccessible")
            return original_open(path, flags, *args)

        with patch.object(gate_module.os, "open", side_effect=deny_shot):
            result = await self.approve(preview)
        self.assertEqual(result["error"], "screenshot_unavailable")
        self.assertEqual(self.clicks, [])

        async def raises():
            """Represent an exception in the VM screenshot endpoint."""
            raise PermissionError("screen unavailable")

        self.assertFalse((await self.gate.preview(20, 25, "target", raises))["ok"])
        self.assertEqual(self.clicks, [])

    async def test_out_of_bounds_dimensions_block_click(self):
        """Reject oversized decoded images even when their encoded PNG is tiny."""
        from unittest.mock import patch
        with patch.object(gate_module, "MAX_SCREEN_PIXELS", 100):
            result = await self.gate.preview(20, 25, "target", self.screenshot)
        self.assertFalse(result["ok"])
        self.assertEqual(self.clicks, [])

    async def test_uncertain_receipt_burns_ticket(self):
        """Never replay after an ambiguous VM click response or exception."""
        preview = await self.checkpoint()

        async def uncertain(x, y):
            """Represent a failed VM API response with unknown physical outcome."""
            self.clicks.append((x, y))
            return {"ok": False}

        kwargs = dict(x=20, y=25, screen_sha256=preview["screen_sha256"], viewed=True)
        result = await self.gate.confirm(preview["ticket"], self.screenshot, uncertain, **kwargs)
        self.assertEqual(result["error"], "click_outcome_uncertain")
        self.assertEqual((await self.gate.confirm(preview["ticket"], self.screenshot,
                          self.click, **kwargs))["error"], "no_pending_preview")
        self.assertEqual(self.clicks, [(20, 25)])

    async def test_concurrent_confirmation_clicks_once(self):
        """Lock entire confirmation so two callers cannot race one ticket."""
        preview = await self.checkpoint()
        results = await asyncio.gather(self.approve(preview), self.approve(preview))
        self.assertEqual(sum(result["ok"] for result in results), 1)
        self.assertEqual(self.clicks, [(20, 25)])


if __name__ == "__main__":
    unittest.main()
