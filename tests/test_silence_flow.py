"""Offline regression tests for silence at real production reply/send boundaries.

Reuse the AST-loaded integration fixture rather than importing startup services or
copying production logic. Provider responses, images, and delivery stay local.
"""

import asyncio
import json
from unittest.mock import Mock

import pytest

from test_production_integration import (
    Event,
    Image,
    _call,
    _provider,
    production as _production_fixture,
)
from plugin.message_batch import message_record

# Register the existing fixture without duplicating its production-loading behavior.
production = _production_fixture


async def _no_forward(event):
    """Exclude unrelated forwarded-card lookup without bypassing message admission."""
    return ""


@pytest.fixture
def silence_plugin(production):
    """Retain production handling while making wake and reply-marking observable."""
    plugin = production()
    # Wake bypasses cooldown so a missing reply cannot be mistaken for rate limiting.
    plugin._is_woken = Mock(return_value=True)
    plugin._wake_hit = Mock(return_value="")
    plugin._forward_to_text = _no_forward
    plugin._mark_replied = Mock(wraps=plugin._mark_replied)
    return plugin


def _assert_quiet(plugin, event):
    """Reject delivery, phantom bot history, cooldown marks, and sent-ID mutation."""
    assert event.sent == []
    assert not any(line.startswith("彼岸酱：") for line in plugin._hist().load("42"))
    assert not any(line.startswith("彼岸酱：") for line in plugin._recent.get("42", []))
    plugin._mark_replied.assert_not_called()
    assert plugin._last_reply_at == {}
    assert plugin._reply_times == {}
    assert plugin._last_sent == {}
    assert not getattr(plugin, "_my_recent", {})


def _assert_single_completion(plugin, event, requests):
    """Require one native model completion and no fabricated tool/action follow-up."""
    assert len(requests) == 1
    assert event.actions == []
    assert plugin._tool_evidence().recent("42") == []
    assert all(message["role"] not in {"tool", "assistant"}
               for message in requests[0]["messages"])


@pytest.mark.parametrize("sentinel", ["SILENT", "silent", "Silent.!", "（SILENT）"])
def test_structured_silence_on_message_current_batch(silence_plugin, monkeypatch, sentinel):
    """Validated current-batch attribution must never turn a sentinel into a message."""
    plugin, event = silence_plugin, Event("please decide whether to reply")
    requests = _provider(monkeypatch, [(json.dumps({"reply_to": [1234], "text": sentinel}), [])])
    asyncio.run(plugin.on_message(event))
    _assert_quiet(plugin, event)
    _assert_single_completion(plugin, event, requests)
    # Only the incoming user record is durable; silence must not erase that input.
    assert len(plugin._hist().load("42")) == 1
    assert event.stopped
    assert plugin._busy == {"42": False}


@pytest.mark.parametrize("sentinel", ["SILENT", "silent", "Silent.!", "（SILENT）"])
def test_structured_silence_handle_one_current_batch(silence_plugin, monkeypatch, sentinel):
    """Direct handling uses the frozen batch's real IDs and produces no send effects."""
    plugin, event = silence_plugin, Event()
    plugin._turn_context["42"] = {"messages": [message_record(event, event.text)]}
    requests = _provider(monkeypatch, [(json.dumps({"reply_to": [1234], "text": sentinel}), [])])
    asyncio.run(plugin._handle_one(event, "42", event.text, False, True))
    _assert_quiet(plugin, event)
    _assert_single_completion(plugin, event, requests)
    assert plugin._hist().load("42") == []


def test_json_string_silence_on_message(silence_plugin, monkeypatch):
    """A single JSON-string wrapper is control output, not quoted visible prose."""
    plugin, event = silence_plugin, Event()
    requests = _provider(monkeypatch, [('"silent"', [])])
    asyncio.run(plugin.on_message(event))
    _assert_quiet(plugin, event)
    _assert_single_completion(plugin, event, requests)


@pytest.mark.parametrize("sentinel", ["silent", "（SILENT）"])
def test_plain_silence_on_message(silence_plugin, monkeypatch, sentinel):
    """Plain native completion ends the turn, including the observed wrapped leak."""
    plugin, event = silence_plugin, Event()
    requests = _provider(monkeypatch, [(sentinel, [])])
    asyncio.run(plugin.on_message(event))
    _assert_quiet(plugin, event)
    _assert_single_completion(plugin, event, requests)


@pytest.mark.parametrize("sentinel", ["SILENT", "silent", "Silent.!", '"silent"', "（SILENT）"])
def test_direct_send_rejects_sentinel_before_cleaning(silence_plugin, sentinel):
    """Even callers bypassing reply selection cannot deliver a raw control token."""
    plugin, event = silence_plugin, Event()
    plugin._strip_tool_syntax = Mock(side_effect=AssertionError("sentinel reached cleanup"))
    assert asyncio.run(plugin._send(event, "42", sentinel)) is None
    plugin._strip_tool_syntax.assert_not_called()
    _assert_quiet(plugin, event)


def test_direct_send_rejects_silence_exposed_by_cleanup(silence_plugin):
    """The post-clean boundary rejects a sentinel hidden behind removable tool text."""
    plugin, event = silence_plugin, Event()
    # Stub only cleanup's output to isolate its contract from syntax-parser changes.
    plugin._strip_tool_syntax = Mock(return_value="silent")
    assert asyncio.run(plugin._send(event, "42", "wrapped tool syntax")) is None
    plugin._strip_tool_syntax.assert_called_once_with("wrapped tool syntax")
    _assert_quiet(plugin, event)


def test_direct_send_rejects_silence_exposed_by_quote_removal(silence_plugin):
    """The final-text guard catches control output after legacy quote extraction."""
    plugin, event = silence_plugin, Event()
    assert asyncio.run(plugin._send(event, "42", "[quote:1234] silent")) is None
    _assert_quiet(plugin, event)


def test_normal_silent_mode_prose_is_preserved(silence_plugin, monkeypatch):
    """Substring mentions of silence remain ordinary prose and send exactly once."""
    plugin, event = silence_plugin, Event()
    answer = "silent mode is available"
    requests = _provider(monkeypatch, [(answer, [])])
    asyncio.run(plugin.on_message(event))
    _assert_single_completion(plugin, event, requests)
    assert len(event.sent) == 1
    assert [component.text for component in event.sent[0].chain if hasattr(component, "text")] == [answer]
    assert plugin._hist().load("42")[-1] == "彼岸酱：" + answer
    assert plugin._last_sent == {"42": ["sent-1"]}
    plugin._mark_replied.assert_called_once_with("42")


def test_image_only_marker_is_delivered_without_caption(silence_plugin, tmp_path):
    """Empty visible text is allowed when a real local image component was attached."""
    plugin, event = silence_plugin, Event()
    image = tmp_path / "offline-image.png"
    image.write_bytes(b"offline fixture: Image.fromFileSystem never decodes bytes")
    assert asyncio.run(plugin._send(event, "42", f"[发图:{image}]")) == "[图片]"
    assert len(event.sent) == 1
    assert len(event.sent[0].chain) == 1
    assert isinstance(event.sent[0].chain[0], Image)
    assert event.sent[0].chain[0].file == str(image)
    assert plugin._hist().load("42") == ["彼岸酱：[图片]"]
    assert plugin._last_sent == {"42": ["sent-1"]}
    plugin._mark_replied.assert_called_once_with("42")


@pytest.mark.parametrize("targets", [[9999], [1234, 9999], [True], "1234", []])
def test_structured_invalid_silence_targets_fail_closed(silence_plugin, monkeypatch, targets):
    """A sentinel cannot legitimize nonexistent, mixed, mistyped, or absent targets."""
    plugin, event = silence_plugin, Event()
    requests = _provider(monkeypatch, [(json.dumps({"reply_to": targets, "text": "silent"}), [])])
    asyncio.run(plugin.on_message(event))
    _assert_quiet(plugin, event)
    _assert_single_completion(plugin, event, requests)
    assert not getattr(plugin, "_last_reply_selection", None)


def test_silence_after_native_tool_has_no_extra_completion(silence_plugin, monkeypatch):
    """An accepted tool completes once; its silent final adds no reply or extra calls."""
    plugin, event = silence_plugin, Event()
    requests = _provider(monkeypatch, [
        ("", [_call("self_status", call_id="silence-status")]),
        ("silent", []),
    ])
    asyncio.run(plugin.on_message(event))
    _assert_quiet(plugin, event)
    assert len(requests) == 2
    records = plugin._tool_evidence().recent("42")
    assert len(records) == 1
    assert records[0]["tool_name"] == "self_status"
    tool_messages = [message for message in requests[-1]["messages"] if message["role"] == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "silence-status"
    assert event.actions == []
