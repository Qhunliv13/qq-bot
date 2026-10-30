"""Offline production-entry checks for history origin integration, not helper copies."""

import ast
import asyncio
import copy
import json

from test_production_integration import (
    ROOT, Event, _empty_forward, _provider, production,
)

# Re-export the existing fixture explicitly without introducing a second loader.
__all__ = ["production"]


def test_length_bounded_incoming_and_confirmed_replies_keep_serialized_prefix(production, monkeypatch):
    """Three real sends retain all small source entries without a count-based live eviction."""
    plugin = production()
    plugin._recent = {"42": [f"old-{number}" for number in range(1000, 3000)]}
    plugin._history_origins = {"42": 1000}
    plugin._forward_to_text = _empty_forward
    plugin._is_woken = lambda event: True
    plugin._wake_hit = lambda *args: ""
    requests = _provider(monkeypatch, [(f"reply-{number}", []) for number in range(3)])
    expected_histories = []

    async def handle(event, group, text, has_image, woken):
        """Exercise actual context construction, provider transport, and confirmed send."""
        expected_histories.append(tuple(plugin._recent[group][:-1][-120:]))
        prompt = plugin._request_context(event, group, text, has_image)
        answer, calls = await plugin._llm_tools(plugin.persona, prompt, [])
        assert calls == []
        await plugin._send(event, group, answer)

    plugin._handle_one = handle

    async def scenario():
        """Use one event loop as the real admission lock requires."""
        events = [Event(f"current-{number}", str(6000 + number)) for number in range(3)]
        for event in events:
            await plugin.on_message(event)
        assert all(len(event.sent) == 1 for event in events)

    asyncio.run(scenario())
    assert len(requests) == 3
    prompts = [request["messages"][1]["content"] for request in requests]
    envelopes = [json.loads(prompt) for prompt in prompts]
    histories = [envelope["conversation"]["history"] for envelope in envelopes]
    assert [len(history) for history in histories] == [2000, 2002, 2004]
    for index, history in enumerate(histories):
        assert tuple(history[-120:]) == expected_histories[index]
        assert history[:2000] == [f"old-{number}" for number in range(1000, 3000)]
        assert envelopes[index]["conversation"]["messages"][0]["body"] == f"current-{index}"
        assert not any(f"current-{index}" in entry for entry in history)
        assert prompts[index].count(f"current-{index}") == 1
    # Encode the known structure and remove only its closing delimiters: raw
    # history may itself contain JSON-looking text, and later lists append rows.
    assert all(envelope["observations"] == envelopes[0]["observations"] for envelope in envelopes)
    stable = {"version": envelopes[0]["version"], "source": envelopes[0]["source"],
              "observations": envelopes[0]["observations"],
              "conversation": {"history": histories[0]}}
    stable_prefix = json.dumps(stable, ensure_ascii=False, separators=(",", ":"))[:-3].encode("utf-8")
    assert all(prompt.encode("utf-8").startswith(stable_prefix) for prompt in prompts)
    assert plugin._history_origins == {"42": 1000}
    assert len(plugin._recent["42"]) == 2006
    assert plugin._recent["42"][-1] == "彼岸酱：reply-2"


def test_restore_200_history_uses_derived_window_without_losing_latest(production):
    """Real first-message disk restore supplies all 200 existing entries as history."""
    plugin = production()
    for number in range(200):
        plugin._hist().append("42", f"restored-{number}")
    plugin._forward_to_text = _empty_forward
    plugin._is_woken = lambda event: True
    plugin._wake_hit = lambda *args: ""
    captured = []

    async def handle(event, group, text, has_image, woken):
        """Inspect the real frozen batch context without generating a model response."""
        captured.append(json.loads(plugin._request_context(event, group, text, has_image)))

    plugin._handle_one = handle
    asyncio.run(plugin.on_message(Event("new-input")))
    assert len(captured) == 1
    assert captured[0]["conversation"]["history"] == [f"restored-{number}" for number in range(200)]
    assert plugin._history_origins == {"42": 0}
    assert len(plugin._recent["42"]) == 201


def test_membership_changes_advance_same_history_origin(production):
    """Actual member-diff method uses the same trim accounting as messages/replies."""
    plugin = production()
    source = ROOT / "plugin" / "main.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    klass = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "HiganPlugin")
    method = copy.deepcopy(next(node for node in klass.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "_diff_members"))
    # Load only this extra production method into the same controlled globals as
    # the established fixture; no AstrBot import or startup registration occurs.
    namespace = plugin.on_message.__func__.__globals__.copy()
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), str(source), "exec"), namespace)
    plugin._diff_members = namespace["_diff_members"].__get__(plugin)
    plugin._recent = {"42": [f"old-{number}" for number in range(2000)]}
    plugin._history_origins = {"42": 1000}
    plugin._member_snapshot = {"42": {"7": "leaving"}}

    async def onebot(action, payload):
        """Return synthetic group membership without a platform connection."""
        assert action == "get_group_member_list" and payload == {"group_id": 42}
        return {"data": [{"user_id": 8, "nickname": "joining"}]}

    plugin._onebot = onebot
    asyncio.run(plugin._diff_members("42", "offline group"))
    assert plugin._history_origins == {"42": 1000}
    assert len(plugin._recent["42"]) == 2002
    assert plugin._recent["42"][-2:] == ["【群动态】joining（QQ 8）加入了群聊", "【群动态】leaving（QQ 7）退出了群聊"]
    assert plugin._recent["42"][0] == "old-0"


def test_reload_copies_history_origin_with_live_buffer(production):
    """Real previous-instance drain retains logical phase instead of resetting to zero."""
    old, new = production(), production()
    old._recent = {"42": [f"old-{number}" for number in range(2000)]}
    old._history_origins = {"42": 1777}
    old._busy = {"42": False}

    async def _watch_members(self, started):
        """Expose a real old-instance owner frame in the expected idle watcher shape."""
        started.set()
        await asyncio.sleep(60)
        return self

    async def scenario():
        """Keep ownership discovery and deterministic cancellation within one loop."""
        started = asyncio.Event()
        task = asyncio.create_task(_watch_members(old, started))
        try:
            await started.wait()
            await new._drain_previous_instances()
            assert task.cancelled()
            assert new._recent == old._recent
            assert new._history_origins == {"42": 1777}
            assert new._history_origins is not old._history_origins
            new._history_origins["42"] += 1
            assert old._history_origins == {"42": 1777}
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
