"""Synthetic adapters only: no credentials, QQ host, network or production model."""

import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock

from bot_core.harness import Harness, RunResult
from plugin.agent_loop import ModelCompletionUnavailable


def call(name="inspect", args='{"value": 1}', call_id="one"):
    """Build a native tool call without any production transport dependency."""
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": args}}


class HarnessTests(unittest.IsolatedAsyncioTestCase):
    """Check wrapper boundaries while exercising the real shared agent loop."""

    async def test_native_multiple_rounds(self):
        """Keep cumulative native call/result pairing across multiple rounds."""
        model = AsyncMock(side_effect=[("checking", [call()]),
                                      ("again", [call("read", call_id="two")]),
                                      ("done", [])])
        tool = AsyncMock(return_value={"ok": True, "value": 7})
        result = await Harness(system="sys", llm_messages=model, run_tool=tool).run("ask")
        self.assertIsInstance(result, RunResult)
        self.assertEqual((result.text, result.used_tools), ("done", ("inspect", "read")))
        self.assertEqual([m["role"] for m in result.transcript],
                         ["system", "user", "assistant", "tool", "assistant", "tool", "assistant"])
        self.assertEqual(result.transcript[3]["tool_call_id"], "one")
        self.assertTrue(all(c.kwargs["allow_tools"] for c in model.call_args_list))
        json.dumps(asdict(result))

    async def test_legacy_adapter(self):
        """Use the unchanged system/prompt legacy contract."""
        model = AsyncMock(return_value=("legacy", []))
        result = await Harness(system="sys", llm=model).run(" ask ")
        self.assertEqual(result.text, "legacy")
        self.assertEqual(model.call_args.args[0], "sys")
        self.assertEqual(json.loads(model.call_args.args[1])[0]["content"], " ask ")

    async def test_native_takes_precedence(self):
        """Never invoke the legacy adapter when a native adapter was supplied."""
        legacy = AsyncMock(side_effect=AssertionError("unused"))
        native = AsyncMock(return_value=("native", []))
        await Harness(system="", llm=legacy, llm_messages=native).run("")
        legacy.assert_not_awaited()

    async def test_silent_final(self):
        """Empty model completion does not force publishing or another call."""
        model = AsyncMock(return_value=("", []))
        result = await Harness(system="", llm_messages=model).run("")
        self.assertEqual(result.text, "")
        model.assert_awaited_once()

    async def test_missing_model(self):
        """Fail at construction when no callable model was injected."""
        with self.assertRaises(TypeError):
            Harness(system="s")

    async def test_invalid_adapters(self):
        """Reject explicitly noncallable model/tool adapters before execution."""
        for field in ("llm", "llm_messages", "run_tool"):
            with self.subTest(field=field), self.assertRaises(TypeError):
                Harness(system="s", **{"llm": AsyncMock(), field: 42})

    async def test_strict_system(self):
        """Do not coerce host configuration into an unexpected system prompt."""
        with self.assertRaises(TypeError):
            Harness(system=None, llm=AsyncMock())

    async def test_strict_prompt(self):
        """Do not silently stringify an unintended prompt object."""
        harness = Harness(system="s", llm=AsyncMock())
        with self.assertRaises(TypeError):
            await harness.run(None)

    async def test_fresh_turns(self):
        """Do not retain conversation state between independent requests."""
        model = AsyncMock(return_value=("ok", []))
        harness = Harness(system="s", llm_messages=model)
        first = await harness.run("first")
        second = await harness.run("second")
        self.assertEqual(len(first.transcript), 3)
        self.assertEqual(len(second.transcript), 3)
        self.assertEqual(second.transcript[1]["content"], "second")

    async def test_detached_transcript(self):
        """Nested result mutation cannot alter the caller's execution evidence."""
        external = []
        model = AsyncMock(side_effect=[("", [call()]), ("done", [])])
        result = await Harness(system="s", llm_messages=model).run("q", transcript=external)
        self.assertEqual(result.transcript, external)
        result.transcript[2]["tool_calls"][0]["function"]["name"] = "changed"
        self.assertEqual(external[2]["tool_calls"][0]["function"]["name"], "inspect")

    async def test_nonempty_transcript_rejected(self):
        """Prevent implicit history mixing without mutating caller evidence."""
        model = AsyncMock()
        evidence = [{"role": "user", "content": "old"}]
        with self.assertRaises(ValueError):
            await Harness(system="s", llm_messages=model).run("q", transcript=evidence)
        self.assertEqual(len(evidence), 1)
        model.assert_not_awaited()

    async def test_transcript_type(self):
        """Require a mutable list when the caller asks for partial evidence."""
        with self.assertRaises(TypeError):
            await Harness(system="s", llm=AsyncMock()).run("q", transcript=())

    async def test_unavailable_tool_is_failure(self):
        """No default tool runner may claim successful execution."""
        model = AsyncMock(side_effect=[("", [call()]), ("done", [])])
        result = await Harness(system="s", llm_messages=model).run("q")
        observation = json.loads(result.transcript[3]["content"])
        self.assertEqual(observation["result"], {"ok": False, "error": "tool_unavailable"})
        self.assertEqual(observation["status"], "error")

    async def test_invalid_arguments(self):
        """Keep shared argument validation without invoking malformed requests."""
        tool = AsyncMock()
        model = AsyncMock(side_effect=[("", [call(args="[]")]), ("done", [])])
        result = await Harness(system="s", llm_messages=model, run_tool=tool).run("q")
        tool.assert_not_awaited()
        self.assertEqual(result.used_tools, ())
        self.assertEqual(json.loads(result.transcript[3]["content"])["result"]["error"],
                         "invalid_tool_arguments")

    async def test_tool_failure_not_replayed(self):
        """Record a thrown tool once as failure rather than retrying side effects."""
        tool = AsyncMock(side_effect=RuntimeError("broken"))
        model = AsyncMock(side_effect=[("", [call()]), ("done", [])])
        result = await Harness(system="s", llm_messages=model, run_tool=tool).run("q")
        tool.assert_awaited_once()
        self.assertEqual(result.used_tools, ("inspect",))
        self.assertEqual(json.loads(result.transcript[3]["content"])["status"], "error")

    async def test_provider_failure_preserves_typed_error_and_evidence(self):
        """Propagate original finalization error and retain supplied partial evidence."""
        tool = AsyncMock(return_value={"ok": True})
        model = AsyncMock(side_effect=[("", [call()]), RuntimeError("provider")])
        evidence = []
        with self.assertRaises(ModelCompletionUnavailable) as caught:
            await Harness(system="s", llm_messages=model, run_tool=tool,
                          max_rounds=1).run("q", transcript=evidence)
        self.assertEqual(caught.exception.used_tools, ("inspect",))
        self.assertIsInstance(caught.exception.__cause__, RuntimeError)
        self.assertEqual(evidence[3]["role"], "tool")
        self.assertFalse(any(m["role"] == "assistant" and not m.get("tool_calls")
                             for m in evidence))
        tool.assert_awaited_once()

    async def test_siblings_and_completion_callback(self):
        """Trusted completion waits for all siblings and observation callbacks."""
        observations = AsyncMock()
        completion = AsyncMock(side_effect=[True, False])
        model = AsyncMock(return_value=("", [call(), call("second", call_id="two")]))
        tool = AsyncMock(return_value={"ok": True})
        result = await Harness(system="s", llm_messages=model, run_tool=tool,
                               observation_callback=observations).run(
                                   "context", completion_check=completion,
                                   observation_question="original")
        self.assertEqual(result.used_tools, ("inspect", "second"))
        self.assertEqual(result.text, "")
        self.assertEqual(observations.await_count, 2)
        self.assertEqual(completion.await_count, 2)
        self.assertEqual(observations.call_args_list[0].args[0]["question"], "original")
        model.assert_awaited_once()

    async def test_explicit_limit_forwarded(self):
        """Forward caller limits so shared loop owns no-tools finalization."""
        model = AsyncMock(side_effect=[("", [call()]), ("final", [])])
        result = await Harness(system="s", llm_messages=model, max_rounds=1).run("q")
        self.assertEqual(result.text, "final")
        self.assertEqual([c.kwargs["allow_tools"] for c in model.call_args_list], [True, False])

    async def test_fifo_lock_covers_model_tool_and_callback(self):
        """Queued turns cannot enter the model until the prior callback completes."""
        events = []
        entered = asyncio.Event()
        release = asyncio.Event()
        callback_entered = asyncio.Event()
        callback_release = asyncio.Event()
        lock = asyncio.Lock()

        async def model(messages, allow_tools):
            """Pause the first turn so the second deterministically queues."""
            prompt = messages[1]["content"]
            if messages[-1]["role"] == "tool":
                events.append(prompt + ":final")
                return "done", []
            events.append(prompt + ":model")
            if prompt == "first":
                entered.set()
                await release.wait()
            return "", [call()]

        async def tool(name, args):
            """Record that each queued model request precedes its own tool."""
            events.append("tool")
            return {"ok": True}

        async def observe(record):
            """Hold the first observation callback inside the whole-turn lock."""
            events.append(record["question"] + ":callback")
            if record["question"] == "first":
                callback_entered.set()
                await callback_release.wait()

        harness = Harness(system="s", llm_messages=model, run_tool=tool,
                          observation_callback=observe, lock=lock)
        self.assertIs(harness._lock, lock)
        first = asyncio.create_task(harness.run("first"))
        await entered.wait()
        second = asyncio.create_task(harness.run("second"))
        third = asyncio.create_task(harness.run("third"))
        await asyncio.sleep(0)
        release.set()
        await callback_entered.wait()
        self.assertNotIn("second:model", events)
        callback_release.set()
        await asyncio.gather(first, second, third)
        self.assertEqual(events, ["first:model", "tool", "first:callback", "first:final",
                                  "second:model", "tool", "second:callback", "second:final",
                                  "third:model", "tool", "third:callback", "third:final"])

    async def test_import_has_no_qq_framework_side_effects(self):
        """Fresh-process import must not load host frameworks or QQ transport."""
        code = ("import sys; import bot_core.harness; "
                "assert not any(n.split('.')[0] in {'astrbot','nonebot','qq_transport'} "
                "for n in sys.modules); assert 'plugin' in sys.modules; "
                "assert getattr(sys.modules['plugin'], '__file__', None) is None")
        completed = subprocess.run([sys.executable, "-c", code],
                                   cwd=Path(__file__).resolve().parents[1],
                                   capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
