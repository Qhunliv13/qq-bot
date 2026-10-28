"""Production native-request preparation must not monopolize the event loop."""

import ast
import asyncio
import copy
from pathlib import Path
import threading
import time

import pytest

import test_production_integration as integration

# Compile the same selected production methods without importing AstrBot startup.
production = integration.production


@pytest.mark.parametrize("phase", ["snapshot", "initial_image", "continuation_image"])
def test_native_preparation_keeps_heartbeat_and_image_prefix(production, monkeypatch, phase):
    """Real blocking preparation yields while native images and tool pairs stay frozen."""
    from plugin.cross_scope_memory import CrossScopeMemory

    plugin, event = production(), integration.Event()
    entered, release = threading.Event(), threading.Event()
    state = {"ticks": 0, "during": 0, "thread": None}
    converted = []
    original_snapshot = CrossScopeMemory.unified_snapshot

    def blocking_io():
        """Bound a deliberately stalled worker so regressions cannot deadlock pytest."""
        state["thread"] = threading.get_ident()
        before = state["ticks"]
        entered.set()
        # The loop releases this gate after observable progress; the timeout also
        # makes the pre-fix synchronous implementation fail rather than hang.
        release.wait(timeout=0.5)
        time.sleep(0.02)
        state["during"] = state["ticks"] - before

    def snapshot(self, *args, **kwargs):
        """Exercise the real memory result after the selected blocking read edge."""
        if phase == "snapshot":
            blocking_io()
        return original_snapshot(self, *args, **kwargs)

    async def initial_refs(*args):
        """Provide an original attachment without downloading any external image."""
        return ["initial"], ""

    def convert(ref):
        """Stall only the selected batch and detect any reconversion of old images."""
        converted.append(ref)
        if (phase == "initial_image" and ref == "initial") or (
                phase == "continuation_image" and ref == "shot"):
            blocking_io()
        return "data:image/png;base64," + ref

    async def dispatch(name, args, scope="", event=None):
        """Return a native screenshot observation without invoking a real VM."""
        assert name == "vm_shot"
        return {"ok": True, "path": "shot"}

    monkeypatch.setattr(CrossScopeMemory, "unified_snapshot", snapshot)
    plugin._refs_for = initial_refs
    plugin._as_llm_url = convert
    plugin._dispatch_tool = dispatch
    requests = integration._provider(monkeypatch, [
        ("", [integration._call("vm_shot", {}, "shot-call")]),
        ("finished", []),
    ])

    async def run():
        """Keep a real loop heartbeat alive concurrently with production preparation."""
        loop_thread = threading.get_ident()
        finished = asyncio.Event()

        async def heartbeat():
            """Release blocked I/O only after multiple independent loop iterations."""
            after_entry = 0
            while not finished.is_set():
                state["ticks"] += 1
                if entered.is_set():
                    after_entry += 1
                    if after_entry >= 5:
                        release.set()
                await asyncio.sleep(0.001)

        task = asyncio.create_task(heartbeat())
        try:
            result = await plugin._decide_tools_first(event, "42", event.text, True)
        finally:
            release.set()
            finished.set()
            await task
        assert result == "finished"
        assert entered.is_set(), "selected production preparation was not exercised"
        assert state["thread"] != loop_thread, "blocking preparation ran on event-loop thread"
        assert state["during"] >= 5, "heartbeat stalled during blocking preparation"

    asyncio.run(run())
    assert converted == ["initial", "shot"]
    assert len(requests) == 2
    first = requests[0]["messages"]
    assert requests[1]["messages"][:len(first)] == first
    assert first[1]["content"][-1] == {
        "type": "image_url", "image_url": {"url": "data:image/png;base64,initial"}}
    assert [message["role"] for message in requests[1]["messages"]] == [
        "system", "user", "assistant", "tool", "user"]
    assert requests[1]["messages"][3]["tool_call_id"] == "shot-call"
    assert requests[1]["messages"][-1]["content"][-1] == {
        "type": "image_url", "image_url": {"url": "data:image/png;base64,shot"}}


def test_native_preparation_awaits_offload_before_committing_messages(monkeypatch):
    """Both image batches copy input and commit only after the worker succeeds."""
    path = Path(integration.ROOT) / "plugin" / "main.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    host = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                and node.name == "HiganPlugin")
    decide = next(node for node in host.body if isinstance(node, ast.AsyncFunctionDef)
                  and node.name == "_decide_tools_first")
    callback = next(node for node in ast.walk(decide)
                    if isinstance(node, ast.AsyncFunctionDef) and node.name == "_llm_messages")
    offloads = [node for node in ast.walk(decide) if isinstance(node, ast.Await)
                and isinstance(node.value, ast.Call)
                and ast.unparse(node.value.func) == "asyncio.to_thread"]
    assert any("unified_snapshot" in ast.unparse(node.value.args[0]) for node in offloads)
    image_offloads = [node for node in offloads
                      if ast.unparse(node.value.args[0]) == "with_images"]
    assert len(image_offloads) == 2
    assert all(ast.unparse(node.value.args[1]) == "list(messages)" for node in image_offloads)

    # Execute the unchanged callback in isolation so an actual worker failure can
    # prove that the caller's native message list is never partially committed.
    async def exercise():
        """Inject a failing converter while retaining the production callback AST."""
        from plugin import image_transcript

        messages = [{"role": "user", "content": "original native text"}]
        before = copy.deepcopy(messages)

        def failed_conversion(*args, **kwargs):
            """Mutate only the worker's list before emulating a failed image read."""
            args[0].append({"role": "user", "content": "must not leak"})
            raise OSError("offline image read failed")

        namespace = dict(integration._load_production(path.parent.parent).on_message.__globals__)
        # The wrapper creates the exact nonlocal state expected by the nested
        # production function; no production module or global asyncio patching.
        wrapper = ast.parse(
            "def build():\n"
            "    images_initialized = False\n"
            "    submitted_ref_count = 1\n"
            "    initial_refs = ['initial']\n"
            "    live_refs = ['initial']\n"
            "    task_context = None\n"
            "    turn = {}\n"
            "    MAX_IMAGES_PER_CALL = 3\n"
            "    self = host\n"
            "    system = prompt = ''\n"
            "    return None\n")
        wrapper.body[0].body[-1:] = [copy.deepcopy(callback), ast.Return(ast.Name("_llm_messages", ast.Load()))]
        # Attribute resolution needs the same minimal host surface as production.
        namespace["host"] = type("Host", (), {"_as_llm_url": staticmethod(str)})()
        exec(compile(ast.fix_missing_locations(wrapper), str(path), "exec"), namespace)
        with monkeypatch.context() as patch:
            patch.setattr(image_transcript, "with_images", failed_conversion)
            with pytest.raises(OSError, match="offline image read failed"):
                await namespace["build"]()(messages, True)
        assert messages == before

    asyncio.run(exercise())
