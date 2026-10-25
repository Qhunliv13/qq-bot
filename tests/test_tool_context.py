"""Offline contract tests for tool evidence persistence and native/legacy loops."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import sys

import pytest

# Import only the pure plugin namespace, not the framework-dependent main entry.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugin import agent_loop  # noqa: E402
from plugin.tool_context import (  # noqa: E402
    ToolContextArchive, bounded_value, make_observation, observations_prompt, redact,
)


def test_persistence_recent_scope_and_retry(tmp_path):
    """Scope archives survive re-instantiation and retain newest records only."""
    archive = ToolContextArchive(tmp_path, max_records=2)
    for number in range(4):
        archive.append("../../scope A", "original question", "lookup", {"n": number}, {"n": number}, observation_id=str(number))
    archive.append("scope B", "other question", "lookup", {}, {"other": True})
    restored = ToolContextArchive(tmp_path, max_records=2)
    records = restored.recent("../../scope A")
    assert [item["result"]["n"] for item in records] == [2, 3]
    assert records[-1]["question"] == "original question"
    assert records[-1]["tool_name"] == "lookup"
    assert records[-1]["arguments"] == {"n": 3}
    assert "+00:00" in records[-1]["timestamp"]
    assert restored.recent("missing") == []
    assert restored.recent("scope B")[0]["result"] == {"other": True}
    assert restored.recent("../../scope A", 0) == []
    assert len(restored.recent("../../scope A", 1)) == 1
    duplicate = restored.append("../../scope A", "retry", "lookup", {}, {}, observation_id="3")
    assert duplicate == records[-1]
    assert restored.recent("../../scope A") == records
    assert len(list(tmp_path.glob("*.jsonl"))) == 2
    assert not list(tmp_path.glob(".observations-*"))


def test_recursive_secret_redaction_and_no_mutation():
    """Credential keys and known inline credential formats are redacted anywhere."""
    secret = "sk-" + "a" * 32
    value = {"api_key": "private", "nested": [{"TOKEN": "private", "Password": "private", "Authorization": "Bearer abc", "cookie": "session=x", "client_secret": "private", "ssh_private_key": "private"}], "text": f"key {secret} Bearer abc.def token=private", "token_count": 42}
    original = copy.deepcopy(value)
    redacted = redact(value)
    encoded = json.dumps(redacted)
    assert "private" not in encoded.replace("ssh_private_key", "").replace("client_secret", "")
    assert secret not in encoded
    assert "abc.def" not in encoded
    assert redacted["token_count"] == 42
    assert value == original


@pytest.mark.parametrize("value", [
    '"\\\n雪' * 2000, {"items": ["雪\\\"" * 1000]}, ["x" * 5000],
])
def test_bounded_value_valid_json(value):
    """Oversized escaped strings and structured output stay valid strict JSON."""
    bounded = bounded_value(value, 160)
    encoded = json.dumps(bounded, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    assert len(encoded) <= 160
    assert json.loads(encoded)["truncated"] is True


def test_string_embedded_credentials_are_redacted(tmp_path):
    """Credentials embedded in JSON strings and URL auth never reach persisted disk."""
    secret = "opaque-secret-value"
    payload = json.dumps({"api_key": secret, "password": secret})
    archive = ToolContextArchive(tmp_path)
    archive.append("s", payload, "lookup", {"nested": [{"X-Auth-Token": secret}]}, {"text": payload, "url": "https://user:private-password@example.invalid/path"})
    persisted = next(tmp_path.glob("*.jsonl")).read_text(encoding="utf-8")
    assert secret not in persisted
    assert "private-password" not in persisted
    assert "[REDACTED]" in persisted


def test_bounded_prompt_newest_and_observation_framing():
    """Prompt includes newest fitting records and explicitly avoids fact promotion."""
    records = [make_observation("q", "lookup", {}, {"value": n}) for n in range(30)]
    prompt = observations_prompt(records, 1100)
    assert len(prompt) <= 1100
    parsed = json.loads(prompt)
    assert parsed["kind"] == "tool_observations"
    assert "not verified current facts" in parsed["instruction"]
    assert parsed["observations"][-1]["result"]["value"] == 29
    assert parsed["omitted"] > 0
    small = observations_prompt([make_observation("q" * 8000, "x", {}, "y" * 8000)], 256)
    assert len(small) <= 256
    json.loads(small)


def test_disk_result_and_record_bounds(tmp_path):
    """Configured bounds limit every serialized result and total per-scope file."""
    archive = ToolContextArchive(tmp_path, max_records=3, max_result_chars=100, max_record_chars=1024)
    for _ in range(9):
        archive.append("s", "q" * 4000, "x" * 4000, {"long": "\\" * 8000}, {"payload": "雪" * 4000})
    path = next(tmp_path.glob("*.jsonl"))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert all(len(line) <= 1024 for line in lines)
    assert path.stat().st_size <= 3 * (1024 * 4 + 1)
    for line in lines:
        record = json.loads(line)
        assert len(json.dumps(record["result"], ensure_ascii=False, separators=(",", ":"))) <= 100


def test_corrupt_tail_and_oversized_lines_are_ignored(tmp_path):
    """Corruption is ignored while earlier/later valid observations remain usable."""
    archive = ToolContextArchive(tmp_path, max_record_chars=1024)
    archive.append("s", "q", "tool", {}, {"ok": True})
    path = next(tmp_path.glob("*.jsonl"))
    valid = path.read_text(encoding="utf-8")
    path.write_text(valid + "not-json\n" + "x" * 10000 + "\n" + valid + '{"unfinished":', encoding="utf-8")
    assert len(archive.recent("s")) == 2


def test_failure_status_preserved_and_non_json_sanitized(tmp_path):
    """Generic explicit failures cannot accidentally be relabeled success."""
    archive = ToolContextArchive(tmp_path)
    for result in [{"ok": False}, {"success": False}, {"error": "failed"}, {"status": "timeout"}]:
        record = archive.append("s", "q", "tool", {}, result, status="success")
        assert record["status"] == "error"
    value = [float("nan"), object()]
    value.append(value)
    json.dumps(redact(value), allow_nan=False)


def test_threaded_append_retains_all_records(tmp_path):
    """Shared local locking prevents concurrent archive instances losing writes."""
    def append(number):
        """Create separate host instances to exercise the shared process lock."""
        return ToolContextArchive(tmp_path).append("s", "q", "tool", {}, number)
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(append, range(24)))
    assert sorted(item["result"] for item in ToolContextArchive(tmp_path).recent("s")) == list(range(24))


def _call(name, args="{}", call_id=None):
    """Build a small provider tool-call fixture with optional explicit identity."""
    return {"id": call_id, "function": {"name": name, "arguments": args}}


def test_native_multiple_tools_rounds_and_final_pass():
    """Every native tool result is paired and retained through final no-tools call."""
    transcript, snapshots, observations = [], [], []
    replies = [("pretool draft", [_call("a", call_id="fixed"), _call("b")]), ("another draft", [_call("c")]), ("observed safe final", [])]

    async def native(messages, allow_tools=True):
        """Record immutable native transcript snapshots instead of a mutable alias."""
        snapshots.append((copy.deepcopy(messages), allow_tools))
        return replies.pop(0)

    async def legacy(system, prompt):
        """Native mode must never use the legacy transport."""
        raise AssertionError("legacy called")

    async def tool(name, arguments):
        """Return distinct structured production-style evidence for each call."""
        return {"name": name, "arguments": arguments, "ok": True}

    result = asyncio.run(agent_loop.run("system", "original question", legacy, tool, 2,
                                        llm_messages=native, transcript=transcript,
                                        observation_callback=observations.append))
    assert result == ("observed safe final", ["a", "b", "c"])
    assert [snapshot[1] for snapshot in snapshots] == [True, True, False]
    assert len([m for m in snapshots[1][0] if m["role"] == "tool"]) == 2
    assert len([m for m in snapshots[2][0] if m["role"] == "tool"]) == 3
    requested_ids = [c["id"] for m in transcript for c in m.get("tool_calls", [])]
    assert requested_ids[0] == "fixed"
    assert requested_ids == [m["tool_call_id"] for m in transcript if m["role"] == "tool"]
    assert len(set(requested_ids)) == 3
    assert all(observation["question"] == "original question" for observation in observations)
    assert transcript[-1]["content"] == "observed safe final"


@pytest.mark.parametrize("raw", ["{broken", "[]", "null", '"string"', None, 3])
def test_malformed_arguments_never_execute(raw):
    """Invalid arguments produce explicit error observations, not an empty invocation."""
    observations = []
    replies = [("unsafe draft", [_call("mutating_tool", raw)]), ("invalid arguments reported", [])]

    async def llm(system, prompt):
        """Supply a malformed call, followed by a user-facing safe final."""
        return replies.pop(0)

    async def tool(name, args):
        """Any invocation here is a safety regression."""
        raise AssertionError("must not execute")

    answer, used = asyncio.run(agent_loop.run("s", "q", llm, tool, 1, observation_callback=observations.append))
    assert answer == "invalid arguments reported"
    assert used == []
    assert observations[0]["status"] == "error"
    assert observations[0]["result"]["error"] == "invalid_tool_arguments"


def test_legacy_cumulative_evidence_and_tool_failure():
    """Legacy callers get cumulative valid JSON, including runner failure evidence."""
    prompts, observations = [], []
    replies = [("draft", [_call("a"), _call("b")]), ("draft2", [_call("c")]), ("final", [])]

    async def llm(system, prompt):
        """Capture the cumulative textual transport for round-to-round assertions."""
        prompts.append(json.loads(prompt))
        return replies.pop(0)

    async def tool(name, args):
        """One tool failure must not erase another tool's observation."""
        if name == "b":
            raise RuntimeError("password=private failure")
        return {"value": name}

    result = asyncio.run(agent_loop.run("s", "q", llm, tool, 2, observation_callback=observations.append))
    assert result == ("final", ["a", "b", "c"])
    assert len([m for m in prompts[1] if m["role"] == "tool"]) == 2
    assert len([m for m in prompts[2] if m["role"] == "tool"]) == 3
    assert observations[1]["status"] == "error"
    assert "private" not in json.dumps(observations)


def test_exhausted_unexecuted_draft_and_provider_tool_violation():
    """Finalization rejects unexecuted drafts and ignores further requested execution."""
    invocations, flags = [], []

    async def llm(system, prompt):
        """Legacy callback is supplied only to preserve the original interface."""
        raise AssertionError("unexpected legacy")

    async def native(messages, allow_tools=True):
        """Deliberately violate the final tools-disabled instruction."""
        flags.append(allow_tools)
        return "I executed everything", [_call("side_effect")]

    async def tool(name, args):
        """Record exactly the tools the loop actually executes."""
        invocations.append(name)
        return {"ok": True}

    answer, used = asyncio.run(agent_loop.run("s", "q", llm, tool, 1, llm_messages=native))
    assert answer == agent_loop.SAFE_FINAL
    assert flags == [True, False]
    assert invocations == used == ["side_effect"]


def test_native_secret_arguments_are_redacted_but_tool_receives_original():
    """Execution receives real args while persisted/native model evidence omits secrets."""
    transcript, received = [], []
    secret = "opaque-credential-that-has-no-known-prefix"
    replies = [("draft", [_call("a", json.dumps({"api_key": secret, "nested": {"refresh_token": secret}}), "same"), _call("b", {}, "same")]), ("final", [])]

    async def native(messages, allow_tools=True):
        """Provide duplicate ids to exercise safe native result pairing."""
        return replies.pop(0)

    async def tool(name, args):
        """Capture originals separately, returning an echo that also needs redaction."""
        received.append(args)
        return {"echo": args, "ok": True}

    async def legacy(*args):
        """Native execution must not fall back to textual transport."""
        raise AssertionError("legacy called")

    asyncio.run(agent_loop.run("s", "q", legacy, tool, 1, transcript=transcript, llm_messages=native))
    assert received[0]["api_key"] == secret
    assert secret not in json.dumps(transcript)
    ids = [m["tool_call_id"] for m in transcript if m["role"] == "tool"]
    assert len(set(ids)) == 2
    assert ids[0] == "same"


def test_model_round_failure_gets_final_call():
    """An initial model failure still permits a constrained safe final answer."""
    flags = []

    async def native(messages, allow_tools=True):
        """Fail the first request and recover only for the final no-tools pass."""
        flags.append(allow_tools)
        if allow_tools:
            raise RuntimeError("transient provider error")
        return "No observations available", []

    async def unused(*args):
        """No tool or legacy callback is allowed on this failure path."""
        raise AssertionError("unexpected callback")

    assert asyncio.run(agent_loop.run("s", "q", unused, unused, llm_messages=native)) == ("No observations available", [])
    assert flags == [True, False]


@pytest.mark.parametrize("max_rounds", [None, 0, -1])
def test_unbounded_rounds_allow_model_completion(max_rounds):
    """Absent or nonpositive limits allow thirty tool rounds before model completion."""
    flags = []

    async def native(messages, allow_tools=True):
        """Keep requesting tools beyond the former ceiling, then explicitly stop."""
        flags.append(allow_tools)
        if len(flags) <= 30:
            return "working", [_call("lookup")]
        return "model finished", []

    async def tool(name, args):
        """Return offline evidence without introducing any external operations."""
        return {"ok": True}

    async def unused(*args):
        """Native mode must never invoke the legacy transport."""
        raise AssertionError("unexpected legacy")

    result = asyncio.run(agent_loop.run("s", "q", unused, tool, max_rounds, llm_messages=native))
    assert result == ("model finished", ["lookup"] * 30)
    assert flags == [True] * 31


def test_model_and_callback_failure_are_safe():
    """Persistence failure cannot lose results, and final LLM failure cannot leak draft."""
    calls = 0
    transcript = []

    async def llm(system, prompt):
        """Produce one tool round before a final provider outage."""
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("provider offline")
        return "draft must not escape", [_call("a")]

    async def tool(name, args):
        """Return successful evidence independent of observation storage availability."""
        return {"ok": True}

    async def callback(record):
        """Simulate a full archive volume without breaking the conversation."""
        raise OSError("archive unavailable")

    result = asyncio.run(agent_loop.run("s", "q", llm, tool, 1, transcript=transcript, observation_callback=callback))
    assert result == (agent_loop.SAFE_FINAL, ["a"])
    assert any(m["role"] == "tool" for m in transcript)


def test_explicit_round_limit_above_twenty_is_honored():
    """A positive caller-selected limit is honored without the former hard clamp."""
    flags, transcript = [], []

    async def native(messages, allow_tools=True):
        """Request tools until the runtime reaches the explicit thirty-round limit."""
        flags.append(allow_tools)
        if allow_tools:
            return "draft", [_call("lookup")]
        assert messages[-1] == {"role": "system", "content": agent_loop.FINAL_INSTRUCTION}
        return "finished at configured limit", []

    async def tool(name, args):
        """Provide deterministic local evidence for each requested operation."""
        return {"ok": True}

    result = asyncio.run(agent_loop.run("s", "q", tool, tool, 30,
                                        llm_messages=native, transcript=transcript))
    assert result == ("finished at configured limit", ["lookup"] * 30)
    assert flags == [True] * 30 + [False]
    assert [m for m in transcript if m["role"] == "user"] == [{"role": "user", "content": "q"}]


def test_default_round_limit_is_model_directed():
    """Omitting the limit allows thirty tool rounds and does not force finalization."""
    calls = 0

    async def llm(system, prompt):
        """Return a finite model-led sequence longer than all former default limits."""
        nonlocal calls
        calls += 1
        return ("draft", [_call("lookup")]) if calls <= 30 else ("done", [])

    async def tool(name, args):
        """Keep this regression test entirely offline."""
        return {"ok": True}

    assert agent_loop.MAX_ROUNDS is None
    assert asyncio.run(agent_loop.run("s", "q", llm, tool)) == ("done", ["lookup"] * 30)
    assert calls == 31


@pytest.mark.parametrize("text", ["", None, "   "])
def test_empty_model_completion_remains_silent(text):
    """No-call empty output is ordinary model silence, not a finalization failure."""
    transcript = []

    async def llm(system, prompt):
        """Return an intentional no-call completion with no visible content."""
        return text, []

    async def unused(*args):
        """A completed model response must not trigger tool execution or retry."""
        raise AssertionError("unexpected execution")

    assert asyncio.run(agent_loop.run("s", "q", llm, unused, transcript=transcript)) == ("", [])
    assert transcript[-1] == {"role": "assistant", "content": ""}


def test_unbounded_upstream_and_finish_failure_stop_without_retry():
    """An upstream outage receives one final attempt, never an unbounded retry loop."""
    flags = []

    async def native(messages, allow_tools=True):
        """Fail both the ordinary request and its one text-only finalization."""
        flags.append(allow_tools)
        raise RuntimeError("offline")

    async def unused(*args):
        """The failure path cannot execute tools or use another transport."""
        raise AssertionError("unexpected execution")

    result = asyncio.run(agent_loop.run("s", "q", unused, unused, llm_messages=native))
    assert result == (agent_loop.SAFE_FINAL, [])
    assert agent_loop.SAFE_FINAL == "本轮未获得可用的最终回复；已执行步骤以工具记录为准。"
    assert flags == [True, False]
