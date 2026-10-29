"""Offline shared checkpoint to actual pointer primitive, with no VM capabilities."""

import asyncio
import copy
from unittest.mock import patch

import pytest
from PIL import Image

from plugin import vm_click_gate as gate_module
from plugin.vm_pointer_runtime import canonical_pointer_action, execute_pointer_path


@pytest.fixture
def harness(tmp_path):
    """Use real PNG pixels but fake command execution and deterministic runtime time."""
    shot = tmp_path / "shot.png"
    Image.new("RGB", (160, 120), "white").save(shot)
    gate = gate_module.ClickGate(tmp_path)
    action = dict(version=1, button="left", width=160, height=120,
                  points=[dict(x=20, y=40, t_ms=0), dict(x=80, y=60, t_ms=100),
                          dict(x=120, y=90, t_ms=200)])
    commands, received = [], []
    now = [0.0]

    async def screenshot():
        """Return actual pixels without interacting with any desktop."""
        return {"ok": True, "path": str(shot)}

    def runner(argv, *, timeout_s):
        """Record argv rather than launching a real command."""
        commands.append((argv, timeout_s))
        return 0

    def sleep(seconds):
        """Advance a fake runtime clock without waiting."""
        now[0] += seconds

    async def execute(stored):
        """Trusted test-only adapter serializes the frozen record for the actual primitive."""
        received.append(stored)
        wire = dict(version=stored.version, button=stored.button,
                    width=stored.width, height=stored.height,
                    points=[dict(x=p.x, y=p.y, t_ms=p.t_ms) for p in stored.points])
        return execute_pointer_path(wire, runner=runner, sleep=sleep, clock=lambda: now[0])

    return gate, action, screenshot, execute, commands, received, shot


async def checkpoint(h, observed=True):
    """Create a path preview and optionally record actual model observation."""
    gate, action, screenshot, *_ = h
    preview = await gate.preview_path(action, "descriptive only", screenshot)
    assert preview["ok"], preview
    if observed:
        assert await gate.mark_observed(preview["path"])
    return preview


async def confirm(h, preview, **overrides):
    """Echo exact canonical wire fields unless testing a particular denial."""
    gate, action, screenshot, execute, *_ = h
    args = dict(action=action, screen_sha256=preview["screen_sha256"], viewed=True)
    args.update(overrides)
    return await gate.confirm_path(preview["ticket"], screenshot, execute, **args)


def test_full_shared_gate_to_runtime_and_replay(harness):
    """The actual runtime executes exactly the frozen approved path, once."""
    async def run():
        """Inspect geometry, frozen callback data and deterministic argv receipts."""
        gate, action, _, _, commands, received, shot = harness
        preview = await checkpoint(harness)
        assert preview["action"] == action
        assert len(preview["legend"]) == len(action["points"])
        with Image.open(preview["path"]) as marker:
            assert marker.size == (160, 120)
            assert marker.getpixel((50, 50)) != (255, 255, 255)
            assert marker.getpixel((13, 40)) == (0, 128, 0)
            assert marker.getpixel((113, 90)) == (0, 0, 255)
        assert Image.open(shot).getpixel((50, 50)) == (255, 255, 255)
        stored = gate._pending[-1]
        preview["action"]["points"][0]["x"] = 99
        assert (await confirm(harness, preview))["ok"]
        assert received[0] is stored
        assert received[0] == canonical_pointer_action(action)
        with pytest.raises(AttributeError):
            received[0].points[0].x = 99
        assert [command for command, _ in commands] == [
            ("xdotool", "mousemove", "20", "40"), ("xdotool", "mousedown", "1"),
            ("xdotool", "mousemove", "80", "60"), ("xdotool", "mousemove", "120", "90"),
            ("xdotool", "mouseup", "1")]
        assert (await confirm(harness, preview))["error"] == "no_pending_preview"
        assert len(received) == 1
    asyncio.run(run())


@pytest.mark.parametrize("field,value", [("x", 21), ("t_ms", 101), ("button", "right"),
    ("version", True), ("width", 161), ("x", True), ("x", 20.0)])
def test_modified_action_consumes_without_commands(harness, field, value):
    """Changed path/time/button and integer lookalikes cannot alias the stored action."""
    async def run():
        """Submit one altered confirmation then prove the ticket was burned."""
        preview = await checkpoint(harness)
        action = copy.deepcopy(harness[1])
        target = action["points"][1] if field in ("x", "t_ms") else action
        target[field] = value
        assert (await confirm(harness, preview, action=action))["error"] == "preview_confirmation_mismatch"
        assert (await confirm(harness, preview))["error"] == "no_pending_preview"
        assert harness[4] == harness[5] == []
    asyncio.run(run())


@pytest.mark.parametrize("override", [{"viewed": False}, {"viewed": 1}, {"screen_sha256": "wrong"}, {"action": None}])
def test_confirmation_fields_fail_closed(harness, override):
    """Every malformed confirmation consumes before any callback is invoked."""
    async def run():
        """Check explicit viewing, digest and schema validation."""
        preview = await checkpoint(harness)
        assert (await confirm(harness, preview, **override))["error"] == "preview_confirmation_mismatch"
        assert (await confirm(harness, preview))["error"] == "no_pending_preview"
        assert not harness[4]
    asyncio.run(run())


def test_no_observed_changed_screen_and_expiry(harness):
    """Observation, decoded pixels and both TTL checks are shared with clicks."""
    async def run():
        """Exercise each freshness denial independently with a new ticket."""
        gate, action, screenshot, execute, commands, _, shot = harness
        preview = await checkpoint(harness, observed=False)
        assert (await confirm(harness, preview))["error"] == "preview_image_not_seen_by_model"
        preview = await checkpoint(harness)
        Image.new("RGB", (160, 120), "black").save(shot)
        assert (await confirm(harness, preview))["error"] == "screen_changed"
        with patch.object(gate_module.time, "monotonic", return_value=100):
            preview = await checkpoint(harness)
        with patch.object(gate_module.time, "monotonic", return_value=161):
            assert (await confirm(harness, preview))["error"] == "preview_expired"
        with patch.object(gate_module.time, "monotonic", return_value=100):
            preview = await checkpoint(harness)
        clock = [150]

        async def late_capture():
            """Cross the TTL while capturing unchanged pixels."""
            clock[0] = 161
            return await screenshot()

        with patch.object(gate_module.time, "monotonic", side_effect=lambda: clock[0]):
            result = await gate.confirm_path(preview["ticket"], late_capture, execute,
                action=action, screen_sha256=preview["screen_sha256"], viewed=True)
        assert result["error"] == "preview_expired"
        assert not commands
    asyncio.run(run())


@pytest.mark.parametrize("first_kind", ["click", "path"])
def test_mixed_kinds_and_repreview_revoke(harness, first_kind):
    """Wrong-kind confirmations consume; new previews replace either kind's slot."""
    async def run():
        """Exercise both crossing directions without calling either executor."""
        gate, action, screenshot, execute, commands, _, _ = harness

        async def click(*args):
            """Fail loudly if a wrong-kind ticket reaches the click adapter."""
            commands.append(args)
            return {"ok": True}

        for replace in (False, True):
            if first_kind == "click":
                old = await gate.preview(20, 40, "click", screenshot)
                await gate.mark_observed(old["path"])
                if replace:
                    await checkpoint(harness)
                result = await confirm(harness, old)
            else:
                old = await checkpoint(harness)
                if replace:
                    await gate.preview(20, 40, "click", screenshot)
                result = await gate.confirm(old["ticket"], screenshot, click, x=20, y=40,
                    screen_sha256=old["screen_sha256"], viewed=True)
            assert result["error"] == "preview_confirmation_mismatch"
            assert gate.pending_marker_path is None
        assert not commands
    asyncio.run(run())


def test_dimension_mismatch_and_invalid_preview_revoke(harness):
    """Even valid coordinates cannot authorize a different screenshot coordinate space."""
    async def run():
        """Reject dimensions and schemas before any input, revoking the old ticket."""
        gate, action, screenshot, *_ = harness
        for invalid in (dict(action, width=161), dict(action, button="right")):
            old = await checkpoint(harness)
            assert not (await gate.preview_path(invalid, "label", screenshot))["ok"]
            assert (await confirm(harness, old))["error"] == "no_pending_preview"
        assert not harness[4]
    asyncio.run(run())


def test_concurrency_and_mutable_arguments(harness):
    """Lock capture/execution, freeze before capture, and never execute mutable arguments."""
    async def run():
        """Mutation during awaits cannot change approved execution or enable replay."""
        gate, action, screenshot, execute, commands, received, _ = harness
        original = copy.deepcopy(action)

        async def mutate_capture():
            """Mutate caller-owned data after the gate must have copied it."""
            action["points"][0]["x"] = 99
            await asyncio.sleep(0)
            return await screenshot()

        preview = await gate.preview_path(action, "label", mutate_capture)
        assert preview["action"] == original
        assert await gate.mark_observed(preview["path"])
        action["points"][0]["x"] = 20
        first = gate.confirm_path(preview["ticket"], mutate_capture, execute, action=action,
            screen_sha256=preview["screen_sha256"], viewed=True)
        results = await asyncio.gather(first, confirm(harness, preview))
        assert sum(result["ok"] for result in results) == 1
        assert received[0] == canonical_pointer_action(original)
        assert len(commands) == 5
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["exception", "receipt", "capture", "unavailable"])
def test_uncertainty_redacts_and_burns(harness, failure):
    """Uncertain paths never leak backend exception/failed-receipt secrets or retry."""
    async def run():
        """Reject missing capabilities and failures with fixed gate errors."""
        gate, action, screenshot, execute, commands, _, _ = harness
        preview = await checkpoint(harness)

        async def fail(*args):
            """Simulate private diagnostic output from an injected capability."""
            if failure == "receipt":
                return {"ok": False, "secret": "private-token"}
            raise RuntimeError("private-token")

        result = await gate.confirm_path(preview["ticket"],
            fail if failure == "capture" else screenshot,
            None if failure == "unavailable" else fail,
            action=action, screen_sha256=preview["screen_sha256"], viewed=True)
        expected = {"capture": "screenshot_unavailable", "unavailable": "backend_unavailable"}
        assert result == {"ok": False, "error": expected.get(failure, "path_outcome_uncertain")}
        assert (await confirm(harness, preview))["error"] == "no_pending_preview"
        assert not commands
    asyncio.run(run())
