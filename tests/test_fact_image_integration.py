"""Offline fact-image contracts through real production dispatch and native tools."""

import asyncio
import base64
import json

import pytest

# Reuse the AST-loaded production class and transport rather than copy host logic.
import test_production_integration as integration
from plugin.memory import Memory

# A module-level fixture alias registers the original fixture without redefining it.
production = integration.production
Event = integration.Event

# A minimal signature-bearing PNG is sufficient: storage validates bytes, not pixels.
PNG_BYTES = b"\x89PNG\r\n\x1a\nfact-image-integration"
PNG_URL = "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode("ascii")
PNG_METADATA = {"index": 0, "mime": "image/png", "bytes": len(PNG_BYTES)}


@pytest.fixture
def fact_host(production, tmp_path, monkeypatch):
    """Keep every actual dispatch-created Memory beneath the temporary fixture root."""
    host = production()
    monkeypatch.setitem(host._dispatch_tool.__func__.__globals__, "HIGAN_ROOT", str(tmp_path))
    assert host._policy_allows(Event(), "42")
    return host


def _dispatch(host, name, args=None, scope="42"):
    """Use the real dispatcher with an explicit permitted event, never a fake store."""
    return asyncio.run(host._dispatch_tool(name, args or {}, scope, event=Event()))


def test_remember_snapshots_refs_and_persists_canonical_images(fact_host, production, tmp_path):
    """Remote refs become immutable canonical bytes, while list/search expose metadata."""
    converted = []

    def convert(ref):
        """Mimic the existing converter's historically incorrect downloaded MIME label."""
        converted.append(ref)
        return PNG_URL.replace("image/png", "image/jpeg")

    fact_host._as_llm_url = convert
    saved = _dispatch(fact_host, "remember", {"text": "image-backed fact", "images": ["attachment-ref"]})
    assert saved["ok"] is True
    assert saved["id"]
    assert saved["images"] == [PNG_METADATA]
    assert converted == ["attachment-ref"]
    memory_path = tmp_path / "memory" / "memory.json"
    durable = Memory(str(memory_path)).load().data["global"]["facts"]
    assert durable[0]["images"] == [PNG_URL]
    assert "attachment-ref" not in memory_path.read_text(encoding="utf-8")

    # A new production instance must recover actual disk bytes, not an instance cache.
    restored = production()
    listed = _dispatch(restored, "facts_list")["facts"]
    assert listed[0]["id"] == saved["id"]
    assert listed[0]["images"] == [PNG_METADATA]
    assert "base64" not in json.dumps(listed)
    searched = _dispatch(restored, "facts_search", {"query": "image-backed"})
    assert searched["facts"] == listed
    read = _dispatch(restored, "facts_read_image", {"id": saved["id"], "index": 0})
    assert read == {"ok": True, "id": saved["id"], "image": {"ref": PNG_URL, **PNG_METADATA}}


def test_edit_omitted_images_preserves_and_explicit_empty_clears(fact_host):
    """Text edits retain saved bytes; an explicit empty array deliberately removes them."""
    fact_host._as_llm_url = lambda ref: PNG_URL
    saved = _dispatch(fact_host, "remember", {"text": "original", "images": ["original-ref"]})
    assert saved["ok"] is True
    fact_id = saved["id"]
    assert _dispatch(fact_host, "facts_edit", {"id": fact_id, "text": "renamed"})["ok"] is True
    assert _dispatch(fact_host, "facts_read_image", {"id": fact_id})["image"]["ref"] == PNG_URL
    assert _dispatch(fact_host, "facts_list")["facts"][0]["text"] == "renamed"
    assert _dispatch(fact_host, "facts_edit", {"id": fact_id, "text": "cleared", "images": []})["ok"] is True
    assert _dispatch(fact_host, "facts_list")["facts"][0]["images"] == []
    assert _dispatch(fact_host, "facts_read_image", {"id": fact_id}) == {
        "ok": False, "error": "fact_image_not_found"}


def test_hidden_private_fact_is_not_readable_or_editable_before_conversion(fact_host, tmp_path):
    """An ID cannot bypass private visibility or cause hidden attachment acquisition."""
    memory_path = tmp_path / "memory" / "memory.json"
    private_id = Memory(str(memory_path)).load().add_fact(
        "private secret", "private:70001", images=[PNG_URL])
    before = memory_path.read_bytes()
    converted = []

    def convert(ref):
        """Record attempts so privacy is checked before converter side effects."""
        converted.append(ref)
        return PNG_URL

    fact_host._as_llm_url = convert
    assert _dispatch(fact_host, "facts_list")["facts"] == []
    assert _dispatch(fact_host, "facts_read_image", {"id": private_id}) == {
        "ok": False, "error": "fact_image_not_found"}
    assert _dispatch(fact_host, "facts_edit", {
        "id": private_id, "text": "overwrite", "images": ["must-not-fetch"]})["ok"] is False
    assert converted == []
    assert memory_path.read_bytes() == before


@pytest.mark.parametrize("tool", ["remember", "facts_edit"])
def test_unavailable_image_does_not_mutate_any_fact(fact_host, tmp_path, tool):
    """All references must resolve before either creating or editing durable facts."""
    fact_host._as_llm_url = lambda ref: PNG_URL
    saved = _dispatch(fact_host, "remember", {"text": "unchanged", "images": ["good"]})
    assert saved["ok"] is True
    memory_path = tmp_path / "memory" / "memory.json"
    before = memory_path.read_bytes()
    converted = []

    def convert(ref):
        """Resolve one image then fail the second to catch partial writes."""
        converted.append(ref)
        return PNG_URL if ref == "good" else ""

    fact_host._as_llm_url = convert
    result = _dispatch(fact_host, tool, {
        "id": saved["id"], "text": "must not persist", "images": ["good", "unavailable"]})
    assert result["ok"] is False
    assert "image_unavailable" in result["error"]
    assert converted == ["good", "unavailable"]
    assert memory_path.read_bytes() == before


def test_native_fact_image_read_feeds_next_model_without_archiving_base64(fact_host, monkeypatch):
    """The real two-round native loop pairs metadata then appends pixels as visual input."""
    fact_host._as_llm_url = lambda ref: PNG_URL
    saved = _dispatch(fact_host, "remember", {"text": "inspect this", "images": ["source"]})
    assert saved["ok"] is True
    # Transport now accepts only the stored canonical bytes, proving retrieval used them.
    fact_host._as_llm_url = lambda ref: ref if ref == PNG_URL else ""
    call = integration._call("facts_read_image", {"id": saved["id"], "index": 0}, "fact-image-native")
    requests = integration._provider(monkeypatch, [("", [call]), ("I inspected the saved image.", [])])
    event = Event("Inspect the saved fact image")
    answer = asyncio.run(fact_host._decide_tools_first(event, "42", event.text, False))
    assert answer == "I inspected the saved image."
    assert len(requests) == 2
    messages = requests[1]["messages"]
    tool_index = next(index for index, message in enumerate(messages) if message["role"] == "tool")
    tool_message = messages[tool_index]
    assert tool_message["tool_call_id"] == call["id"]
    result = json.loads(tool_message["content"])["result"]
    assert result["ok"] is True
    assert result["image"] == PNG_METADATA
    assert "base64" not in tool_message["content"]
    visual = [part["image_url"]["url"] for message in messages[tool_index + 1:]
              if isinstance(message.get("content"), list)
              for part in message["content"] if part.get("type") == "image_url"]
    assert visual == [PNG_URL]
    assert PNG_URL not in json.dumps(requests[0])
    records = fact_host._tool_evidence().recent("42")
    assert records[-1]["result"]["image"] == PNG_METADATA
    assert "base64" not in json.dumps(records)
    assert event.sent == []
