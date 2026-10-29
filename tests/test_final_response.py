"""Focused tests for fail-closed final replies and untouched intermediate rounds."""

import asyncio
import json
from functools import wraps

import pytest

from plugin.final_response import (
    FINAL_RESPONSE_CONTRACT, FinalResponseUnavailable,
    ensure_final_response, publishable,
)


RECORDS = [{"id": "101"}, {"id": "102"}]


def async_test(test):
    """Run async test bodies without requiring an optional pytest plugin."""
    @wraps(test)
    def wrapper(*args, **kwargs):
        """Run the coroutine in an isolated loop for predictable unit tests."""
        return asyncio.run(test(*args, **kwargs))
    return wrapper


def envelope(text="public answer", ids=None, **fields):
    """Create a current-batch envelope without changing production parsing."""
    return json.dumps({"reply_to": ["101"] if ids is None else ids,
                       "text": text, **fields})


@async_test
async def test_valid_envelope_preserved_including_literal_quoted_planning():
    """Never censor benign quoted words in an authenticated public body."""
    raw = envelope('The memo literally says "planning", not an instruction.')
    async def unwanted(_draft):
        """Reject accidental calls when the first output is already public."""
        raise AssertionError("repair was unnecessary")
    assert await ensure_final_response(raw, [], RECORDS, unwanted) == (raw, [])
    assert publishable(raw, RECORDS)
    assert publishable(envelope(delivery="already_delivered"), RECORDS)
    assert publishable(envelope(delivery="new"), RECORDS)


@async_test
async def test_generic_private_draft_repaired_once_without_keyword_filter():
    """An unstructured English draft stays private even without risk words."""
    seen = []
    async def repair(draft):
        """Record the exact private draft passed into trusted repair."""
        seen.append(draft)
        return envelope("ready"), []
    raw = "Some notes about a fox and a river."
    assert await ensure_final_response(raw, [], RECORDS, repair) == (envelope("ready"), [])
    assert seen == [raw]


@pytest.mark.parametrize("invalid", [
    "a prose draft", "{broken", '["101"]', '"quoted prose"',
    json.dumps({"text": "missing targets"}),
])
@async_test
async def test_invalid_first_output_gets_exactly_one_repair(invalid):
    """Malformed envelopes and false attribution require one new completion."""
    seen = []
    async def repair(draft):
        """Supply a valid final without ever exposing the invalid one."""
        seen.append(draft)
        return envelope("clean"), []
    assert await ensure_final_response(invalid, [], RECORDS, repair) == (envelope("clean"), [])
    assert seen == [invalid]


@pytest.mark.parametrize("invalid", [envelope(ids=["999"]), envelope(ids=[]),
    envelope(delivery="queued"), envelope(ids=["101", "999"]),
])
@async_test
async def test_invalid_structured_targets_fail_without_repair(invalid):
    """Reject invalid current IDs/disposition before another model can change them."""
    async def unwanted(_draft):
        """No second provider call may invent an authorized target."""
        raise AssertionError("invalid target triggered repair")
    with pytest.raises(FinalResponseUnavailable):
        await ensure_final_response(invalid, [], RECORDS, unwanted)


@async_test
async def test_structured_silence_does_not_publish_literal_json():
    """A valid structured silence must remain silent through agent_loop."""
    async def unwanted(_draft):
        """No repair is necessary for a valid empty public text."""
        raise AssertionError("unexpected repair")
    assert await ensure_final_response(envelope(text="SILENT"), [], RECORDS, unwanted) == ("", [])
    assert await ensure_final_response(envelope(text=""), [], RECORDS, unwanted) == ("", [])


@pytest.mark.parametrize("repair_result", [
    ("private text with secrets", []),
    (envelope("clean"), [{"name": "send"}]),
    (envelope(ids=["404"]), []),
    (envelope(delivery="invalid"), []),
])
@async_test
async def test_repair_failure_does_not_leak_or_reexecute(repair_result):
    """Failed repair raises only a fixed code, including tool-bearing output."""
    count = []
    async def repair(draft):
        """Observe one invocation without interpreting returned tool objects."""
        count.append(draft)
        return repair_result
    with pytest.raises(FinalResponseUnavailable) as error:
        await ensure_final_response("private original", [], RECORDS, repair)
    assert len(count) == 1
    assert str(error.value) == "final_response_unavailable"
    assert "private" not in str(error.value)


@async_test
async def test_callback_exception_is_sanitized():
    """Neither callback error text nor draft becomes a public failure."""
    async def repair(_draft):
        """Simulate provider failure containing a sensitive private draft."""
        raise RuntimeError("sensitive private draft")
    with pytest.raises(FinalResponseUnavailable) as error:
        await ensure_final_response("private original", [], RECORDS, repair)
    assert str(error.value) == "final_response_unavailable"
    assert error.value.__cause__ is None


@async_test
async def test_intermediate_round_preserves_both_raw_and_tool_calls():
    """Tool-bearing content stays private to the host's existing tool loop."""
    raw = "internal scratch draft"
    calls = [{"name": "lookup", "arguments": {"q": "x"}}]
    async def unwanted(_draft):
        """Reject any repair during tool-bearing intermediate rounds."""
        raise AssertionError("intermediate repair")
    result = await ensure_final_response(raw, calls, RECORDS, unwanted)
    assert result[0] is raw and result[1] is calls


@pytest.mark.parametrize("raw", ["", " ", "SILENT", "narrative\nSILENT"])
@async_test
async def test_shared_silence_dispositions_never_repaired(raw):
    """Retain all legacy silence forms including terminal multiline control."""
    async def unwanted(_draft):
        """Reject calls on a validated silence disposition."""
        raise AssertionError("silence repair")
    assert await ensure_final_response(raw, [], RECORDS, unwanted) == (raw, [])


@async_test
async def test_no_batch_requires_empty_targets_and_opt_in():
    """Permit actual unattributed tasks but never fabricated message IDs."""
    async def unwanted(_draft):
        """Reject unnecessary repair for a no-batch public envelope."""
        raise AssertionError("unnecessary repair")
    raw = envelope(ids=[])
    assert await ensure_final_response(raw, [], [], unwanted) == (raw, [])
    assert not publishable(raw, [], allow_unattributed=False)
    assert not publishable(envelope(ids=["101"]), [])
    assert "No tools" in FINAL_RESPONSE_CONTRACT
