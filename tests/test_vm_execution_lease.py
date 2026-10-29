"""Offline gate-to-real-adapter tests with a deliberately saturated thread executor."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
import threading
from types import SimpleNamespace

import pytest
from PIL import Image

from plugin import vm_click_gate as gate_module
from plugin.vm_execution_lease import ExecutionLease
from plugin.vm_pointer_adapter import VmPointerAdapter
from plugin.vm_pointer_runtime import execute_pointer_path


def action():
    """Return a short valid path without invoking a desktop capability."""
    return dict(version=1, button="left", width=20, height=20,
                points=[dict(x=1, y=2, t_ms=0), dict(x=3, y=4, t_ms=10)])


@pytest.mark.parametrize("expired", [False, True])
def test_gate_real_adapter_executor_queue(tmp_path, monkeypatch, expired):
    """Thread-queue delay must consume the original gate TTL, not renew it."""
    shot = tmp_path / "shot.png"
    Image.new("RGB", (20, 20), "white").save(shot)
    executable = tmp_path / "inert-command"
    executable.write_text("not executable code", encoding="utf-8")
    executable.chmod(0o700)
    adapter = VmPointerAdapter(executable_path=executable, env={}, cwd=tmp_path)
    now, commands = [100.0], []
    # Patch only the gate's clock binding: runtime scheduling remains monotonic.
    monkeypatch.setattr(gate_module, "time", SimpleNamespace(monotonic=lambda: now[0]))

    def runner(argv, timeout_s, *, _stop=None):
        """Record commands without launching anything, even on Windows."""
        commands.append(argv)
        return 0

    adapter.runner = runner

    async def scenario():
        """Signal actual executor submission before releasing its sole occupied worker."""
        loop = asyncio.get_running_loop()
        queued = asyncio.Event()
        unblock = threading.Event()

        class QueueExecutor(ThreadPoolExecutor):
            """Expose submission, not timing guesses, to the deterministic test."""

            def submit(self, fn, /, *args, **kwargs):
                """Signal once the adapter's worker has actually been enqueued."""
                future = super().submit(fn, *args, **kwargs)
                if getattr(self, "watch", False):
                    loop.call_soon_threadsafe(queued.set)
                return future

        pool = QueueExecutor(max_workers=1)
        blocker = pool.submit(unblock.wait, 3)
        pool.watch = True
        loop.set_default_executor(pool)
        gate = gate_module.ClickGate(tmp_path, ttl_seconds=1)

        async def screenshot():
            """Supply identical real PNG pixels for preview and confirmation."""
            return {"ok": True, "path": str(shot)}

        preview = await gate.preview_path(action(), "path", screenshot)
        assert await gate.mark_observed(preview["path"])
        kwargs = dict(action=action(), screen_sha256=preview["screen_sha256"], viewed=True,
                      authorized_path_callable=adapter.execute_authorized)
        task = asyncio.create_task(gate.confirm_path(
            preview["ticket"], screenshot, adapter, **kwargs))
        try:
            await asyncio.wait_for(queued.wait(), 2)
            assert gate._lock.locked() and adapter._lock.locked()
            assert not commands
            now[0] = 101.0 if expired else 100.5
        finally:
            unblock.set()
        result = await asyncio.wait_for(task, 2)
        assert result["ok"] is not expired
        if expired:
            assert result == {"ok": False, "error": "path_outcome_uncertain"}
            assert not commands  # Not even mouseup before any press was attempted.
        else:
            assert [argv[1] for argv in commands] == ["mousemove", "mousedown", "mousemove", "mouseup"]
        assert not gate._lock.locked() and not adapter._lock.locked()
        assert (await gate.confirm_path(preview["ticket"], screenshot, adapter,
                                       **kwargs))["error"] == "no_pending_preview"
        blocker.result(timeout=1)

    asyncio.run(scenario())


def test_lease_immutable_and_invalid_clock():
    """The deadline is immutable and non-finite trusted clock values fail closed."""
    lease = ExecutionLease(10, lambda: float("nan"))
    with pytest.raises(FrozenInstanceError):
        lease.deadline = 20
    with pytest.raises(RuntimeError):
        lease.check()
    with pytest.raises(ValueError):
        ExecutionLease(float("inf"), lambda: 0)


@pytest.mark.parametrize("fail_press", [False, True])
def test_start_lease_never_blocks_safety_release(fail_press):
    """Expiry after first input cannot suppress release, even if button-down raises."""
    now, commands = [0.0], []
    lease = ExecutionLease(1, lambda: now[0])

    def runner(argv, *, timeout_s):
        """Expire authority during the first input and optionally fail button-down."""
        commands.append(argv)
        now[0] = max(now[0], 2.0)
        if fail_press and argv[1] == "mousedown":
            raise RuntimeError("private failure")
        return 0

    def sleep(seconds):
        """Advance scheduled runtime time without any real waiting."""
        now[0] += seconds

    result = execute_pointer_path(action(), runner=runner, sleep=sleep,
                                  clock=lambda: now[0], before_start=lease.check)
    assert result["ok"] is not fail_press
    assert commands[-1] == ("xdotool", "mouseup", "1")


def test_invalid_start_hook_has_no_input():
    """An invalid optional capability fails closed before invoking the runner."""
    commands = []
    result = execute_pointer_path(action(), runner=lambda *args, **kw: commands.append(args),
                                  sleep=lambda seconds: None, clock=lambda: 0,
                                  before_start=False)
    assert result == {"ok": False, "error": "pointer_runtime_unavailable"}
    assert not commands
