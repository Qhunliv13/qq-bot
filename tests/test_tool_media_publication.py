"""Keep final publication separate from actual tool-media sends, without image deduplication.

Production dispatch, media delivery, sender, transcript and final routing stay real.
Only model/VM/framework transport edges use the shared offline integration fixtures.
"""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from test_production_integration import Event, Image, _call, _provider
from plugin.message_batch import message_record

pytest_plugins = ("test_production_integration",)


@pytest.fixture
def media(production, tmp_path):
    """Create an actual local attachment and retain the real plugin send pipeline."""
    plugin = production()
    path = tmp_path / "same-sticker.png"
    # The framework image constructor is the transport edge; no codec/network runs.
    path.write_bytes(b"offline-image-bytes")
    plugin.manifest = [{"file": path.name, "desc": "custom fish"}]
    plugin._send.__func__.__globals__["STICKER_DIR"] = tmp_path
    plugin._vm_tool = AsyncMock(return_value={
        "ok": True, "deliver": "plugin", "path": str(path), "send_as": "image"})
    event = Event("Send the selected image once")
    plugin._turn_context["42"] = {"messages": [message_record(event, event.text)]}
    return plugin, event, path


def _images(event):
    """Inspect actual outgoing components rather than rendered image placeholders."""
    return [part.file for chain in event.sent for part in chain.chain if isinstance(part, Image)]


def _run(plugin, event):
    """Use the real foreground decision, decode and final-send boundary."""
    asyncio.run(plugin._handle_one(event, "42", event.text, False, True))


@pytest.mark.parametrize("tool", ["send_shot", "send_sticker", "send_file"])
def test_tool_image_then_implicit_final_marker_is_not_a_second_send(media, monkeypatch, tool):
    """A tool receipt makes an unmarked final a draft, not another attachment request."""
    plugin, event, path = media
    args = {"path": str(path)} if tool != "send_sticker" else {"file": path.name}
    requests = _provider(monkeypatch, [
        ("private planning", [_call(tool, args, "media-once")]),
        ("[发图:%s]" % path, []),
    ])
    _run(plugin, event)
    assert _images(event) == [str(path)]
    assert len(event.sent) == 1
    assert len(requests) == 2
    turn = plugin._turn_context["42"]
    assert not turn["_interim_replies"]
    assert len(turn["_deliveries"]) == 1
    assert turn["_deliveries"][0]["delivery"]["status"] == "observed_send_return"
    assert turn["_final_publication"]["publish"] is False
    observation = json.loads(requests[-1]["messages"][-1]["content"])
    assert observation["result"]["delivered"] is True


def test_native_confirmed_tool_media_also_requires_explicit_final(media, monkeypatch):
    """Numeric native message IDs retain the same publication boundary as legacy returns."""
    plugin, event, path = media
    original_send = event.send

    async def native_send(chain):
        """Keep the fixture's actual outgoing chain while returning native receipt evidence."""
        await original_send(chain)
        return [{"message_id": "1441743276"}]

    event.send = native_send
    _provider(monkeypatch, [
        ("", [_call("send_shot", {"path": str(path)})]), ("[发图:%s]" % path, []),
    ])
    _run(plugin, event)
    assert _images(event) == [str(path)]
    delivery = plugin._turn_context["42"]["_deliveries"][0]["delivery"]
    assert delivery["status"] == "native_confirmed"
    assert delivery["message_ids"] == ["1441743276"]


def test_send_exception_does_not_gate_a_later_successful_final(media, monkeypatch):
    """A failed event.send contributes no receipt; an ordinary fallback message may send."""
    plugin, event, path = media
    original_send = event.send
    attempts = []

    async def fail_first(chain):
        """Fail just the media transport and allow the later final text transport."""
        attempts.append(chain)
        if len(attempts) == 1:
            raise RuntimeError("offline send failure")
        return await original_send(chain)

    event.send = fail_first
    _provider(monkeypatch, [
        ("", [_call("send_shot", {"path": str(path)})]),
        ("The image could not be sent.", []),
    ])
    _run(plugin, event)
    assert len(attempts) == 2
    assert _images(event) == []
    assert event.sent[0].chain[-1].text == "The image could not be sent."
    assert "_final_publication" not in plugin._turn_context["42"]


def test_explicit_new_final_can_intentionally_send_the_same_image_again(media, monkeypatch):
    """The publication protocol permits explicit repeat output; no path/hash dedup applies."""
    plugin, event, path = media
    final = json.dumps({"reply_to": ["1234"], "text": "[发图:%s]" % path, "delivery": "new"})
    _provider(monkeypatch, [
        ("", [_call("send_shot", {"path": str(path)})]), (final, []),
    ])
    _run(plugin, event)
    assert _images(event) == [str(path), str(path)]
    assert len(event.sent) == 2


def test_explicit_sibling_send_requests_are_not_deduplicated(media, monkeypatch):
    """Two native send calls remain two independent public actions, even for one path."""
    plugin, event, path = media
    _provider(monkeypatch, [
        ("", [_call("send_shot", {"path": str(path)}, "first"),
              _call("send_shot", {"path": str(path)}, "second")]),
        ("SILENT", []),
    ])
    _run(plugin, event)
    assert _images(event) == [str(path), str(path)]
    assert plugin._vm_tool.await_count == 2


def test_failed_tool_media_does_not_suppress_an_ordinary_final(media, monkeypatch):
    """A missing attachment creates no actual delivery and cannot convert a final to draft."""
    plugin, event, path = media
    plugin._vm_tool.return_value["path"] = str(path.parent / "missing.png")
    _provider(monkeypatch, [
        ("", [_call("send_shot", {"path": "missing.png"})]),
        ("The attachment was unavailable.", []),
    ])
    _run(plugin, event)
    assert _images(event) == []
    assert len(event.sent) == 1
    assert event.sent[0].chain[-1].text == "The attachment was unavailable."
    assert "_final_publication" not in plugin._turn_context["42"]


@pytest.mark.parametrize("record", [
    None, {}, {"delivery": "not-a-receipt"},
    {"delivery": {"status": "native_confirmed", "destination_scope": "43"}},
    {"delivery": {"status": "failed", "destination_scope": "42"}},
    {"delivery": {"status": "accepted", "destination_scope": "42"}},
])
def test_foreign_malformed_or_non_delivery_records_do_not_gate_final(media, monkeypatch, record):
    """Only actual same-scope sender records enable the explicit publication requirement."""
    plugin, event, _ = media

    async def observe_files(name, args):
        """Insert controlled non-send evidence after per-turn initialization."""
        plugin._turn_context["42"]["_deliveries"].append(record)
        return {"ok": True, "files": []}

    plugin._vm_tool.side_effect = observe_files
    _provider(monkeypatch, [("", [_call("vm_files")]), ("Ordinary final remains public.", [])])
    _run(plugin, event)
    assert len(event.sent) == 1
    assert event.sent[0].chain[-1].text == "Ordinary final remains public."
    assert "_final_publication" not in plugin._turn_context["42"]


def test_previous_turn_media_does_not_gate_a_later_ordinary_final(media, monkeypatch):
    """The existing per-turn reset preserves future replies after an earlier image send."""
    plugin, event, path = media
    _provider(monkeypatch, [
        ("", [_call("send_shot", {"path": str(path)})]), ("SILENT", []),
        ("A new turn has an ordinary answer.", []),
    ])
    _run(plugin, event)
    later = Event("Another question", message_id="1235")
    plugin._turn_context["42"]["messages"] = [message_record(later, later.text)]
    _run(plugin, later)
    assert _images(event) == [str(path)]
    assert len(later.sent) == 1
    assert later.sent[0].chain[-1].text == "A new turn has an ordinary answer."
