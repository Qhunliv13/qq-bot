"""Offline native-loop tests for isolated, scope-bound delegated research."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugin import conversation_context, delegated_runner, delegation  # noqa: E402
from plugin.history_window import history_snapshot  # noqa: E402


def tool(name, **args):
    """Build actual OpenAI tool-call records for the shared native loop."""
    return {"id": "call_" + name, "type": "function", "function": {
        "name": name, "arguments": json.dumps(args),
    }}


class Plugin:
    """Minimal host records actual callback messages without framework imports."""

    def __init__(self, responses):
        self.persona = "persona metadata unchanged\n"
        self._recent = {"a": ["historical participant"]}
        self._history_origins = {"a": 0}
        self._turn_context = {"scope": "parent", "messages": [{"id": "parent"}]}
        self.responses = iter(responses)
        self.requests = []
        self.dispatched = []
        self.catalog = [{"type": "function", "function": {
            "name": name, "parameters": {"type": "object", "properties": {}},
        }} for name in sorted(delegated_runner.READ_ONLY_TOOLS | {
            "workspace_write", "workspace_read_image", "workspace_cwd", "vm_exec",
            "qq_send", "skill_save", "agent_start", "task_create",
        })]

    def _clock_context(self):
        """Supply the same parent-owned clock contract used in ordinary turns."""
        return {"now": "2026-10-27T08:00:00+08:00"}

    def _tool_schemas(self):
        """Return a shared catalog so tests detect accidental schema mutation."""
        return self.catalog

    async def _llm_tools(self, system, prompt, images, *, messages, allow_tools, tool_schemas):
        """Snapshot every real model request and return the next offline response."""
        self.requests.append(deepcopy({"system": system, "prompt": prompt,
                                      "images": images, "messages": messages,
                                      "allow_tools": allow_tools, "schemas": tool_schemas}))
        return next(self.responses)

    async def _dispatch_tool(self, name, args, scope, event=None):
        """Record tool execution; no network, archive or QQ delivery exists here."""
        self.dispatched.append((name, deepcopy(args), scope, event))
        return {"ok": True, "body": "actual evidence " + name}


async def empty_inbox():
    """Represent the manager's asynchronous drain when no updates are pending."""
    return []


def test_three_native_requests_preserve_evidence_and_parent_context():
    """Read, follow-up read and final all share correctly paired private messages."""
    plugin = Plugin([("", [tool("workspace_read", path="notes.txt")]),
                     ("", [tool("web_fetch", url="https://invalid.test")]),
                     ('{"reply_to":[],"text":"Evidence summary"}', [])])
    original = deepcopy(plugin._turn_context)
    original_catalog = deepcopy(plugin.catalog)
    assert asyncio.run(delegated_runner.run_worker(plugin, "research", "a", empty_inbox)) == "Evidence summary"
    assert len(plugin.requests) == 3
    assert [m["role"] for m in plugin.requests[-1]["messages"]] == [
        "system", "user", "assistant", "tool", "assistant", "tool"]
    messages = plugin.requests[-1]["messages"]
    assert messages[3]["tool_call_id"] == messages[2]["tool_calls"][0]["id"]
    assert "actual evidence workspace_read" in messages[3]["content"]
    assert "actual evidence web_fetch" in messages[5]["content"]
    assert plugin.requests[0]["system"] == conversation_context.system_prompt(plugin.persona)
    assert plugin._turn_context == original and plugin.catalog == original_catalog
    assert all(event is None for _, _, _, event in plugin.dispatched)
    assert {s["function"]["name"] for s in plugin.requests[0]["schemas"]} == delegated_runner.READ_ONLY_TOOLS


@pytest.mark.parametrize("name", ["workspace_write", "workspace_read_image", "workspace_cwd", "vm_exec", "qq_send", "skill_save", "agent_start", "task_create", "unknown"])
def test_hard_read_only_denies_even_unadvertised_model_requests(name):
    """Provider hallucination cannot invoke effects or return child image pixels."""
    plugin = Plugin([("", [tool(name)]), ("done", [])])
    assert asyncio.run(delegated_runner.run_worker(plugin, "task", "a", empty_inbox)) == "done"
    assert plugin.dispatched == []
    observation = json.loads(plugin.requests[1]["messages"][-1]["content"])
    assert "permission_denied" in json.dumps(observation)


def test_scope_read_binding_and_discovery_catalog():
    """Cross-scope requests fail closed and discovery reports only executable reads."""
    plugin = Plugin([("", [tool("history_search", keyword="one"),
                           tool("history_search", scope="other"),
                           tool("tool_observations"),
                           tool("tool_observations", scope="other"),
                           tool("tool_search", query=""),
                           tool("tool_describe", names=["workspace_write"])]), ("done", [])])
    asyncio.run(delegated_runner.run_worker(plugin, "task", "a", empty_inbox))
    assert [name for name, _, _, _ in plugin.dispatched] == ["history_search", "tool_observations"]
    assert all(args["scope"] == scope == "a" for _, args, scope, _ in plugin.dispatched)
    records = plugin.requests[1]["messages"]
    discovery = next(m for m in records if m.get("tool_call_id") == "call_tool_search")
    assert '"workspace_write"' not in discovery["content"]
    assert '"workspace_read"' in discovery["content"]
    assert "unknown_tool" in records[-1]["content"]


def test_inbox_updates_wait_for_completed_tool_pairs():
    """Queued task data follows tool results and never interrupts a live request."""
    async def scenario():
        """Queue one update while the first native model callback is awaiting."""
        entered, release = asyncio.Event(), asyncio.Event()
        queued = []
        plugin = Plugin([("", [tool("workspace_read")]), ("done", [])])
        native = plugin._llm_tools

        async def model(*args, **kwargs):
            """Hold only the first request while the task inbox receives an item."""
            if not plugin.requests:
                entered.set()
                await release.wait()
            return await native(*args, **kwargs)

        async def inbox():
            """Drain once without converting updates into participant records."""
            items = queued[:]
            queued.clear()
            return items

        plugin._llm_tools = model
        worker = asyncio.create_task(delegated_runner.run_worker(plugin, "task", "a", inbox))
        await entered.wait()
        queued.append("untrusted follow-up")
        release.set()
        assert await worker == "done"
        assert len(plugin.requests[0]["messages"]) == 2
        messages = plugin.requests[1]["messages"]
        assert [message["role"] for message in messages[-3:]] == ["assistant", "tool", "user"]
        data = json.loads(messages[-1]["content"])
        assert data == {"source": {"name": "delegation_inbox", "trust": "untrusted_data"}, "items": ["untrusted follow-up"]}

    asyncio.run(scenario())


def test_parallel_workers_have_private_transcripts_and_schema_snapshots():
    """One overlapping provider's schema normalization cannot race another worker."""
    async def scenario():
        """Meet at a barrier so both native callbacks are live concurrently."""
        plugin = Plugin([])
        seen = []
        both = asyncio.Event()

        async def model(system, prompt, images, *, messages, allow_tools, tool_schemas):
            """Mutate only one request snapshot after both workers have entered."""
            seen.append((messages, tool_schemas, json.loads(prompt)["task"]["goal"]))
            if len(seen) == 2:
                both.set()
            await both.wait()
            if seen[0][0] is messages:
                tool_schemas[0]["function"]["name"] = "request_local_change"
            return "done", []

        original = deepcopy(plugin.catalog)
        plugin._llm_tools = model
        assert await asyncio.gather(*(delegated_runner.run_worker(plugin, task, "a", empty_inbox)
                                      for task in ("one", "two"))) == ["done", "done"]
        assert seen[0][0] is not seen[1][0] and seen[0][1] is not seen[1][1]
        assert seen[0][1][0]["function"]["name"] == "request_local_change"
        assert seen[1][1][0]["function"]["name"] != "request_local_change"
        assert plugin.catalog == original

    asyncio.run(scenario())


def test_empty_task_has_no_fake_human_and_history_uses_logical_origin():
    """Context carries task data without synthesizing a current conversation item."""
    plugin = Plugin([("done", [])])
    plugin._recent["a"] = [str(i) for i in range(2000)]
    plugin._history_origins["a"] = 137
    asyncio.run(delegated_runner.run_worker(plugin, "", "a", empty_inbox))
    context = json.loads(plugin.requests[0]["prompt"])
    assert context["conversation"]["messages"] == []
    assert context["conversation"]["history"] == list(history_snapshot(
        plugin._recent["a"], 137, max_chars=600000))
    assert context["task"] == {"kind": "delegated_research", "goal": "", "read_only": True}
    assert context["observations"] == []


@pytest.mark.parametrize("final, expected", [("internal narration\nSILENT", ""), ('{"reply_to":[],"text":"SILENT"}', ""), ("", "")])
def test_silence_is_not_a_deliverable(final, expected):
    """Shared decoder suppresses terminal silence in raw and structured responses."""
    plugin = Plugin([(final, [])])
    assert asyncio.run(delegated_runner.run_worker(plugin, "task", "a", empty_inbox)) == expected


def test_invalid_targets_fail_manager_receipt_instead_of_completing(tmp_path):
    """Actual manager records attribution protocol errors as failed, not complete."""
    async def scenario():
        """Await the manager's actual task without polling or touching production."""
        plugin = Plugin([('{"reply_to":["fake"],"text":"SILENT"}', [])])
        manager = delegation.Manager(tmp_path)

        async def runner(task, scope, inbox):
            """Use the exact adapter passed by main's manager wiring."""
            return await delegated_runner.run_worker(plugin, task, scope, inbox)

        receipt = await manager.start("task", None, runner, scope="a")
        await manager._jobs[receipt["id"]]["worker"]
        receipt = manager.status(receipt["id"], scope="a")
        assert receipt["status"] == "failed" and receipt["error"] == "RuntimeError"

    asyncio.run(scenario())
