"""Offline evidence attribution without duplicating complete context snapshots."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from plugin.agent_loop import ModelCompletionUnavailable, run
from plugin.tool_context import ToolContextArchive, make_observation


def native_call(identifier):
    """Build a distinct paired tool request without contacting any provider."""
    return {"id": identifier, "type": "function", "function": {
        "name": "read", "arguments": json.dumps({"item": identifier})}}


def test_original_question_keeps_complete_prompt_once_and_archives_consistently(tmp_path):
    """Two rounds retain all source context while native/archive questions agree."""
    prompt = json.dumps({"history": ["完整旧文" * 3000], "current": "原始请求"},
                        ensure_ascii=False)
    question = "原始请求，保留引号\"和换行\n原文。"
    transcript, records, snapshots = [], [], []
    replies = iter([("", [native_call("first")]),
                    ("", [native_call("second")]), ("完成", [])])
    archive = ToolContextArchive(tmp_path)

    async def model(messages, allow_tools):
        """Freeze each request so later transcript appends cannot hide rewriting."""
        snapshots.append(json.loads(json.dumps(messages, ensure_ascii=False)))
        return next(replies)

    def persist(record):
        """Use the production archive API with the original initiating question."""
        records.append(record)
        archive.append("scope", question, record["tool_name"], record["arguments"],
                       record["result"], observation_id=record["id"])

    runner = AsyncMock(side_effect=[{"ok": True, "value": "第一项"},
                                    {"ok": True, "value": "第二项"}])
    result = asyncio.run(run("persona", prompt, AsyncMock(), runner,
                             llm_messages=model, transcript=transcript,
                             observation_callback=persist, observation_question=question))
    assert result == ("完成", ["read", "read"])
    assert runner.await_count == 2
    assert len(snapshots) == 3
    assert all(snapshot[1] == {"role": "user", "content": prompt}
               for snapshot in snapshots)
    native = [json.loads(message["content"]) for message in transcript
              if message["role"] == "tool"]
    assert native == records
    assert [record["question"] for record in native] == [question, question]
    assert [record["question"] for record in archive.recent("scope")] == [question, question]
    assert [record["result"]["value"] for record in native] == ["第一项", "第二项"]
    assert [message["tool_call_id"] for message in transcript
            if message["role"] == "tool"] == ["first", "second"]
    assert [message["role"] for message in transcript] == [
        "system", "user", "assistant", "tool", "assistant", "tool", "assistant"]


@pytest.mark.parametrize("provided", [False, True])
def test_legacy_default_and_explicit_none_preserve_existing_question(provided):
    """Omitted or None opt-in preserves exactly the shared builder's old behavior."""
    prompt = "legacy完整上下文" * 1000
    model = AsyncMock(side_effect=[("", [native_call("legacy")]), ("done", [])])
    transcript = []
    optional = {"observation_question": None} if provided else {}
    asyncio.run(run("s", prompt, AsyncMock(), AsyncMock(return_value={"ok": True}),
                    llm_messages=model, transcript=transcript, **optional))
    record = json.loads(next(message["content"] for message in transcript
                             if message["role"] == "tool"))
    expected = make_observation(prompt, "read", {"item": "legacy"}, {"ok": True})
    assert record["question"] == expected["question"]
    assert transcript[1]["content"] == prompt


def test_explicit_empty_question_does_not_fall_back_to_full_prompt():
    """Autonomous turns can explicitly have no initiating human question."""
    model = AsyncMock(side_effect=[("", [native_call("empty")]), ("", [])])
    records = []
    asyncio.run(run("s", "full context", AsyncMock(),
                    AsyncMock(return_value={"ok": True}), llm_messages=model,
                    observation_callback=records.append, observation_question=""))
    assert records[0]["question"] == ""


@pytest.mark.parametrize("fail_final", [False, True])
def test_limited_finalization_never_replays_tools_or_discards_evidence(fail_final):
    """Successful and failed no-tools finalization keep the same paired evidence."""
    final = RuntimeError("provider offline") if fail_final else ("final", [])
    model = AsyncMock(side_effect=[("", [native_call("once")]), final])
    runner = AsyncMock(return_value={"ok": True, "value": "kept"})
    transcript, records = [], []
    operation = run("s", "full original context", AsyncMock(), runner, max_rounds=1,
                    llm_messages=model, transcript=transcript,
                    observation_callback=records.append, observation_question="actual")
    if fail_final:
        with pytest.raises(ModelCompletionUnavailable, match="provider_error"):
            asyncio.run(operation)
    else:
        assert asyncio.run(operation) == ("final", ["read"])
    assert runner.await_count == 1
    assert model.await_count == 2
    assert model.await_args_list[-1].kwargs["allow_tools"] is False
    assert len(records) == 1
    assert records[0]["question"] == "actual"
    assert records[0]["result"] == {"ok": True, "value": "kept"}
    assert transcript[1] == {"role": "user", "content": "full original context"}
    assert len([message for message in transcript if message["role"] == "tool"]) == 1
