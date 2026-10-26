"""Offline notice-versus-chat regressions using unchanged production entry methods.

The HTML filename is inert metadata only: no uploaded file exists, is opened, is
executed, or is downloaded. Existing integration fixtures intercept every provider
request and reject unexpected networking; only this new test module is owned here.
"""

import asyncio
import copy
import json

import pytest

from test_batch_flow import _arrive_while_execution_locked, _entry, _envelope
from test_production_integration import Event, Reply, _provider
from test_production_integration import production as _production_fixture


# Reuse the startup-free host, real recorder and real provider serialization.
production = _production_fixture

# These are synthetic source identifiers, not local paths or executable contents.
FILE = {"id": "offline-upload-1624335508", "name": "网页预览-1624335508.html",
        "size": 7342, "busid": 102}
FILE_MESSAGE_ID = "1624335508"
NOTICE_ID = "45086d82-daf6-4545-8464-83d028106725"
SAME_SECOND = 1793016253  # All fixture events deliberately share one source second.


def _event(*, message_id=NOTICE_ID, text="", notice_type=None, file=None, components=None):
    """Supply canonical getters and the full raw OneBot event, never a wrapped segment list."""
    event = Event(text=text, message_id=message_id, components=components)
    raw = {"post_type": "notice" if notice_type else "message", "time": SAME_SECOND,
           "self_id": 90001, "group_id": 42, "user_id": 10001,
           "message_id": message_id, "message": [], "raw_message": text}
    if notice_type:
        # An empty canonical message mirrors AstrBot's synthetic notice event.
        raw["notice_type"] = notice_type
    else:
        raw["message_type"] = "group"
    if file is not None:
        # Notices carry a top-level file dictionary; genuine messages use segments.
        if notice_type:
            raw["file"] = copy.deepcopy(file)
        else:
            raw["message"] = [{"type": "file", "data": copy.deepcopy(file)}]
    event.message_obj.raw_message = raw
    return event


def _source(line):
    """Decode only the appended source JSON, not nicknames or history rendering prose."""
    source = json.loads(line[line.index('{"source": "message_components"'):])
    assert source["source"] == "message_components"
    assert source["trust"] == "untrusted_data"
    return source


def _assert_idle_notice(plugin, event, requests):
    """Check observable suppression and all queue edges, not just an absent reply."""
    assert event.stopped is True
    assert requests == event.sent == event.actions == []
    assert not any(plugin._pending.values())
    assert not any(plugin._busy.values())
    assert plugin._turn_context == {}


def test_upload_notice_records_explicit_metadata_once_without_model_or_empty_utterance(
        production, monkeypatch):
    """A recognized upload is durable source evidence, never an extra chat opportunity."""
    plugin = production()
    _entry(plugin)
    notice = _event(notice_type="group_upload", file=FILE)
    requests = _provider(monkeypatch, [])

    asyncio.run(plugin.on_message(notice))

    _assert_idle_notice(plugin, notice, requests)
    assert notice.get_messages() == notice.message_obj.message == []
    history = plugin._hist().load("42")
    assert len(history) == 1
    assert plugin._recent["42"] == history
    assert len(plugin._all_recent) == 1
    assert _source(history[0])["segments"] == [{"type": "file", "file": FILE}]
    assert history[0].count(FILE["id"]) == history[0].count(FILE["name"]) == 1
    assert "无正文" not in history[0]
    # Restarted readers must see metadata; in-memory-only preservation is insufficient.
    assert production()._hist().load("42") == history


@pytest.mark.parametrize("notice_type", ["notify", "poke"])
def test_unrelated_empty_notice_stops_before_history_pending_or_model(
        production, monkeypatch, notice_type):
    """Canonical-empty non-upload notices must not invent user speech or invoke tools."""
    plugin = production()
    _entry(plugin)
    notice = _event(notice_type=notice_type)
    notice.message_obj.raw_message.update({"sub_type": "poke", "target_id": 90001})
    requests = _provider(monkeypatch, [])

    asyncio.run(plugin.on_message(notice))

    _assert_idle_notice(plugin, notice, requests)
    assert plugin._hist().load("42") == []
    assert plugin._recent == {} and plugin._all_recent == []


def test_notice_then_native_file_and_quoted_caption_share_one_locked_chat_batch(
        production, monkeypatch):
    """Same-second notice never adds a turn; real file and caption retain their sources."""
    plugin = production()
    _entry(plugin)
    notice = _event(notice_type="group_upload", file=FILE)
    native = _event(message_id=FILE_MESSAGE_ID, file=FILE)
    quote = Reply(id=FILE_MESSAGE_ID, sender_id="10001", sender_nickname="Current speaker",
                  message_str=FILE["name"], chain=[])
    caption = _event(message_id="1624335509", text="网页版的", components=[quote])
    requests = _provider(monkeypatch, [("SILENT", [])])

    async def lifecycle():
        """Use the existing execution lock, never a sleep window, to collect real chat."""
        await plugin.on_message(notice)
        _assert_idle_notice(plugin, notice, requests)
        await _arrive_while_execution_locked(plugin, [native, caption])

    asyncio.run(lifecycle())

    assert len(requests) == 1
    conversation = _envelope(requests[0])
    records = conversation["messages"]
    assert [record["id"] for record in records] == [FILE_MESSAGE_ID, "1624335509"]
    assert _source(records[0]["body"])["segments"] == [dict(type="file", **FILE)]
    assert records[1]["body"] == "网页版的"
    assert records[1]["quote"]["id"] == FILE_MESSAGE_ID
    assert records[1]["quote"]["body"] == FILE["name"]
    prompt = requests[0]["messages"][1]["content"]
    assert isinstance(prompt, str)
    assert FILE["id"] in prompt and FILE["name"] in prompt and "网页版的" in prompt
    assert "无正文" not in prompt
    assert all(event.stopped and not event.actions and not event.sent
               for event in (notice, native, caption))
    assert len(plugin._hist().load("42")) == 3
    assert not any(plugin._pending.values()) and not any(plugin._busy.values())


def test_notice_during_active_chat_never_enters_followup_pending_batch(production, monkeypatch):
    """Busy-state admission must suppress notice turns as well as idle-state admission."""
    plugin = production()
    _entry(plugin)
    chat = _event(message_id="active-chat", text="ordinary active question")
    upload = _event(notice_type="group_upload", file=FILE)
    unrelated = _event(message_id="unrelated-notice", notice_type="notify")
    requests = _provider(monkeypatch, [("SILENT", [])])
    original = plugin._llm_tools

    async def lifecycle():
        """Hold only the real model boundary while notices arrive, releasing on failure."""
        started, release = asyncio.Event(), asyncio.Event()

        async def held_model(*args, **kwargs):
            """Build the real request before exposing the active-turn boundary to arrivals."""
            result = await original(*args, **kwargs)
            started.set()
            await release.wait()
            return result

        plugin._llm_tools = held_model
        active = asyncio.create_task(plugin.on_message(chat))
        try:
            await asyncio.wait_for(started.wait(), 2)
            await plugin.on_message(upload)
            await plugin.on_message(unrelated)
            assert plugin._busy["42"] is True
            assert not any(plugin._pending.values())
            assert upload.stopped and unrelated.stopped
        finally:
            # Always let the real entry finish so a failed assertion cannot leak a task.
            release.set()
            await asyncio.wait_for(active, 2)

    asyncio.run(lifecycle())

    assert len(requests) == 1
    assert [record["id"] for record in _envelope(requests[0])["messages"]] == ["active-chat"]
    history = plugin._hist().load("42")
    assert len(history) == 2 and FILE["name"] in history[-1]
    assert all("unrelated-notice" not in line and "无正文" not in line for line in history)
    assert all(not event.actions and not event.sent for event in (chat, upload, unrelated))
    assert not any(plugin._pending.values()) and not any(plugin._busy.values())


def test_sequential_native_file_and_caption_remain_two_real_chat_opportunities(
        production, monkeypatch):
    """No timestamp-based dedup may discard an actual native message after a notice."""
    plugin = production()
    _entry(plugin)
    notice = _event(notice_type="group_upload", file=FILE)
    native = _event(message_id=FILE_MESSAGE_ID, file=FILE)
    quote = Reply(id=FILE_MESSAGE_ID, sender_id="10001", message_str=FILE["name"], chain=[])
    caption = _event(message_id="caption-sequential", text="网页版的", components=[quote])
    requests = _provider(monkeypatch, [("SILENT", []), ("SILENT", [])])

    async def lifecycle():
        """Complete each real chat before the next arrives, without any wall-clock delay."""
        await plugin.on_message(notice)
        _assert_idle_notice(plugin, notice, requests)
        await plugin.on_message(native)
        await plugin.on_message(caption)

    asyncio.run(lifecycle())

    assert len(requests) == 2
    assert [record["id"] for record in _envelope(requests[0])["messages"]] == [FILE_MESSAGE_ID]
    current = _envelope(requests[1])["messages"]
    assert [record["id"] for record in current] == ["caption-sequential"]
    assert current[0]["body"] == "网页版的" and current[0]["quote"]["id"] == FILE_MESSAGE_ID
    assert FILE["id"] in requests[1]["messages"][1]["content"]
    assert len(plugin._hist().load("42")) == 3
    assert all(event.stopped and not event.actions and not event.sent
               for event in (notice, native, caption))


def test_genuine_empty_message_preserves_existing_chat_path(production, monkeypatch):
    """The new filter is notice-specific, not a blanket ban on empty genuine messages."""
    plugin = production()
    _entry(plugin)
    event = _event(message_id="genuine-empty-message")
    requests = _provider(monkeypatch, [("SILENT", [])])

    asyncio.run(plugin.on_message(event))

    assert len(requests) == 1
    record = _envelope(requests[0])["messages"][0]
    assert record["id"] == "genuine-empty-message" and record["body"] == ""
    assert len(plugin._hist().load("42")) == 1
    assert event.stopped and event.actions == event.sent == []


def test_normal_text_message_remains_exact_and_is_not_notice_filtered(production, monkeypatch):
    """Ordinary raw message text retains its current body and exactly one decision turn."""
    plugin = production()
    _entry(plugin)
    event = _event(message_id="normal-text", text="current plain text stays intact")
    requests = _provider(monkeypatch, [("SILENT", [])])

    asyncio.run(plugin.on_message(event))

    assert len(requests) == 1
    record = _envelope(requests[0])["messages"][0]
    assert record["id"] == "normal-text" and record["body"] == event.text
    assert "message_components" not in record["body"]
    assert len(plugin._hist().load("42")) == 1
    assert event.stopped and event.actions == event.sent == []
