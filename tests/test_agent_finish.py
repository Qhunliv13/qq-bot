"""Offline contracts for host-authorized silent completion after paired tool batches."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from plugin.agent_loop import run


def call(name, args=None, identifier=None):
    """Build native calls without a provider or framework dependency."""
    return {"id": identifier or name, "type": "function", "function": {
        "name": name, "arguments": json.dumps({} if args is None else args)}}


def confirmed_reply(name, args, result):
    """Represent a trusted host policy; generic outputs cannot grant termination."""
    return (name == "reply_now" and result.get("finish") is True
            and result.get("delivered") is True and result.get("ok") is True)


def test_confirmed_reply_finishes_silently_after_all_siblings_and_observations():
    """Every requested sibling executes exactly once and persists before return."""
    async def scenario():
        """Capture transcript pairing and callback ordering in one model batch."""
        transcript, events = [], []
        calls = [call("reply_now", {"text": "actual answer", "finish": True}),
                 call("native_image")]
        model = AsyncMock(return_value=("", calls))

        async def tool(name, args):
            """Return actual delivery or native image evidence without networking."""
            events.append(("tool", name))
            return ({"ok": True, "delivered": True, "finish": True, "text": "actual answer"}
                    if name == "reply_now" else {"ok": True, "image": "local.png"})

        async def persist(record):
            """Prove all evidence is archived before terminal completion returns."""
            events.append(("persist", record))

        async def check(name, args, result):
            """Exercise awaited callbacks for every actual successful invocation."""
            assert events[-1][0] == "persist"
            events.append(("check", name))
            return confirmed_reply(name, args, result)

        result = await run("system", "question", AsyncMock(), tool,
                           max_rounds=1, llm_messages=model, transcript=transcript,
                           observation_callback=persist, completion_check=check)
        assert result == ("", ["reply_now", "native_image"])
        assert model.await_count == 1
        assert [event for event in events if event[0] == "tool"] == [
            ("tool", "reply_now"), ("tool", "native_image")]
        assert [event for event in events if event[0] == "check"] == [
            ("check", "reply_now"), ("check", "native_image")]
        assert len([event for event in events if event[0] == "persist"]) == 2
        assert [item["role"] for item in transcript] == [
            "system", "user", "assistant", "tool", "tool"]
        assert [item["tool_call_id"] for item in transcript[-2:]] == [
            item["id"] for item in transcript[2]["tool_calls"]]
        assert "local.png" in transcript[-1]["content"]
    asyncio.run(scenario())


@pytest.mark.parametrize("callback", [None, confirmed_reply])
def test_arbitrary_tool_finish_keys_do_not_stop_loop(callback):
    """Neither absent host policy nor unrelated JSON can control model generation."""
    model = AsyncMock(side_effect=[("", [call("web_fetch")]), ("real final", [])])
    runner = AsyncMock(return_value={"ok": True, "finish": True, "delivered": True})
    result = asyncio.run(run("s", "p", AsyncMock(), runner,
                             llm_messages=model, completion_check=callback))
    assert result == ("real final", ["web_fetch"])
    assert model.await_count == 2


@pytest.mark.parametrize("result", [
    {"ok": True, "delivered": True},
    {"ok": True, "delivered": False, "finish": True},
    {"ok": False, "delivered": False},
    {"ok": True, "delivered": True, "finish": "true"},
])
def test_nonterminal_reply_preserves_model_continuation(result):
    """Defaults, silent no-ops and failed deliveries keep the multi-reply loop."""
    model = AsyncMock(side_effect=[("", [call("reply_now")]), ("next answer", [])])
    runner = AsyncMock(return_value=result)
    assert asyncio.run(run("s", "p", AsyncMock(), runner, llm_messages=model,
                           completion_check=confirmed_reply)) == ("next answer", ["reply_now"])


def test_invalid_arguments_and_runner_exceptions_do_not_invoke_completion_check():
    """A callback receives actual returned results, never fabricated error records."""
    model = AsyncMock(side_effect=[("", [call("invalid", []), call("failed")]),
                                   ("final", [])])
    runner = AsyncMock(side_effect=RuntimeError("failure"))
    check = AsyncMock(return_value=True)
    transcript, observations = [], []
    result = asyncio.run(run("s", "p", AsyncMock(), runner, llm_messages=model,
                             transcript=transcript, observation_callback=observations.append,
                             completion_check=check))
    assert result == ("final", ["failed"])
    assert runner.await_count == 1
    check.assert_not_awaited()
    assert len(observations) == 2
    assert len([item for item in transcript if item["role"] == "tool"]) == 2


def test_later_sibling_failure_is_preserved_before_silent_completion():
    """Terminal delivery does not discard a failed sibling or replay its effects."""
    model = AsyncMock(return_value=("", [call("reply_now"), call("failed")]))
    runner = AsyncMock(side_effect=[{"ok": True, "delivered": True, "finish": True},
                                    RuntimeError("sibling failed")])
    transcript, observations = [], []
    result = asyncio.run(run("s", "p", AsyncMock(), runner, llm_messages=model,
                             transcript=transcript, observation_callback=observations.append,
                             completion_check=confirmed_reply))
    assert result == ("", ["reply_now", "failed"])
    assert runner.await_count == 2
    assert len(observations) == 2
    assert "tool_execution_failed" in transcript[-1]["content"]
    assert "sibling failed" in transcript[-1]["content"]
    assert model.await_count == 1


def test_callback_failure_does_not_grant_completion():
    """A faulty host predicate logs internally and preserves ordinary continuation."""
    model = AsyncMock(side_effect=[("", [call("reply_now")]), ("final", [])])
    runner = AsyncMock(return_value={"ok": True, "delivered": True, "finish": True})
    check = AsyncMock(side_effect=RuntimeError("predicate failed"))
    assert asyncio.run(run("s", "p", AsyncMock(), runner, llm_messages=model,
                           completion_check=check)) == ("final", ["reply_now"])
