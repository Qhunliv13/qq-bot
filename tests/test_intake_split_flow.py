"""Real native HTTP/entry/archive contracts for length-aware queue partitioning."""

import asyncio
import io
import json
import logging
import urllib.request

import pytest

from test_batch_flow import _entry, _envelope
from test_production_integration import Event

pytest_plugins = ("test_production_integration",)


async def _arrive_while_execution_locked(plugin, events):
    """Use actual ingress behind a held lock; allow real large-history summary work."""
    plugin._execution_lock = asyncio.Lock()
    await plugin._execution_lock.acquire()
    try:
        for event in events:
            await type(plugin).on_message(plugin, event)
        assert len(plugin._pending["42"]) == len(events)
    finally:
        plugin._execution_lock.release()
    while plugin._background_tasks:
        await asyncio.wait_for(asyncio.gather(*tuple(plugin._background_tasks)), 15)


def _http(monkeypatch, answer):
    """Script only the native HTTP boundary; retain real compaction and agent loop."""
    requests = []

    def urlopen(request, timeout):
        """Separate summary generations by their envelope, not by call-count guesses."""
        body = json.loads(request.data.decode("utf-8"))
        assert request.full_url == "https://offline.invalid/v1/chat/completions" and timeout > 0
        requests.append(body)
        prompt = next(item["content"] for item in body["messages"] if item["role"] == "user")
        envelope = json.loads(prompt)
        if "conversation" in envelope:
            ids = [record["id"] for record in envelope["conversation"]["messages"]]
            content = "SILENT" if answer == "SILENT" else json.dumps({
                "reply_to": ids, "text": "Processed " + ",".join(ids), "delivery": "new"})
        else:
            # Summary responses are plain validated summaries, never reply envelopes.
            assert "records" in envelope and "max_summary_chars" in envelope
            content = "Earlier messages remain archived in original arrival order."
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr("plugin.runtime_settings.load_settings", lambda: {
        "context_memory": {"max_context_chars": 60000}})
    return requests


@pytest.mark.parametrize("answer", ["ordinary", "SILENT"])
def test_eighty_queued_messages_execute_ordered_native_batches_and_keep_raw_archive(production, monkeypatch, answer):
    """Actual on_message drains several bounded native calls, with no tool side effects."""
    plugin = production()
    _entry(plugin)
    events = [Event("message-%03d " % index + "x" * 900, str(8000 + index)) for index in range(80)]
    requests = _http(monkeypatch, answer)
    asyncio.run(_arrive_while_execution_locked(plugin, events))
    native = [body for body in requests if _is_conversation(body)]
    assert len(native) > 1
    records = [record for body in native for record in _envelope(body)["messages"]]
    assert [record["id"] for record in records] == [event.message_obj.message_id for event in events]
    assert [record["body"] for record in records] == [event.text for event in events]
    assert all(len(json.dumps(body["messages"], ensure_ascii=False, separators=(",", ":")))
               + len(json.dumps(body.get("tools", []), ensure_ascii=False, separators=(",", ":"))) <= 60000
               for body in requests)
    archived = plugin._hist().load_records("42", limit=1000)
    by_id = {row["record"]["id"]: row["record"] for row in archived
             if row.get("record", {}).get("id") in {event.message_obj.message_id for event in events}}
    assert len(by_id) == 80
    assert all(by_id[event.message_obj.message_id]["body"] == event.text for event in events)
    sent = [chain for event in events for chain in event.sent]
    assert len(sent) == (0 if answer == "SILENT" else len(native))
    assert all(not event.actions and event.stopped for event in events)
    assert not any(plugin._pending.values()) and plugin._turn_context == {} and not plugin._busy["42"]


def _is_conversation(body):
    """Identify native ordinary turns without assuming summary-call ordering."""
    content = next(item["content"] for item in body["messages"] if item["role"] == "user")
    return "conversation" in json.loads(content)


def test_oversized_single_archives_warns_and_later_normal_executes(production, monkeypatch, caplog):
    """No model sees an oversized current input; later valid input still executes."""
    plugin = production()
    _entry(plugin)
    huge, normal = Event("H" * 100000, "huge"), Event("later normal message", "normal")
    requests = _http(monkeypatch, "ordinary")
    with caplog.at_level(logging.WARNING):
        asyncio.run(_arrive_while_execution_locked(plugin, [huge, normal]))
    native = [body for body in requests if _is_conversation(body)]
    assert len(native) == 1
    assert [record["id"] for record in _envelope(native[0])["messages"]] == ["normal"]
    assert "intake_archive_only" in caplog.text and "current_input_exceeds_budget" in caplog.text
    archived = {row["record"].get("id"): row["record"]
                for row in plugin._hist().load_records("42", limit=1000) if row.get("record")}
    assert archived["huge"]["body"] == huge.text and archived["normal"]["body"] == normal.text
    assert huge.sent == [] and len(normal.sent) == 1
    assert not huge.actions and not normal.actions and not any(plugin._pending.values())
