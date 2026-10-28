"""Disk-backed memory contracts at production dispatch and request boundaries."""

import asyncio
import base64
import json
import logging

import pytest

import test_production_integration as integration
from plugin.memory import Memory
from plugin.message_batch import message_record

production = integration.production
Event = integration.Event
# Use harmless signature-bearing bytes: the memory store validates image signatures.
PNG_BYTES = b"\x89PNG\r\n\x1a\npersisted-memory-fixture"
PNG_URL = "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode("ascii")


@pytest.fixture
def memory_host(production, tmp_path, monkeypatch):
    """Bind real AST-loaded production methods to an isolated durable memory root."""
    host = production()
    monkeypatch.setitem(host._dispatch_tool.__func__.__globals__, "HIGAN_ROOT", str(tmp_path))
    assert host._policy_allows(Event(), "42")
    return host


def _dispatch(host, name, args, event=None):
    """Invoke production dispatch with the actual explicit event, including absent events."""
    return asyncio.run(host._dispatch_tool(name, args, "42", event=event))


def _memory(tmp_path):
    """Reload actual disk state so assertions cannot pass through an instance cache."""
    return Memory(str(tmp_path / "memory" / "memory.json")).load()


def _context(host, text="unrelated next request", message_id="next"):
    """Inspect the real user-context envelope rather than reconstructing its projection."""
    event = Event(text, message_id=message_id)
    return json.loads(host._request_context(event, "42", event.text, False))


def test_saved_instruction_present_in_next_request_and_external_edit_reread(memory_host, tmp_path):
    """Every request rereads disk, including independent edits without plugin restart."""
    saved = _dispatch(memory_host, "remember", {
        "text": "Use brief numbered summaries", "structure": {"kind": "instruction"}}, Event())
    assert saved["ok"] and saved["persisted"] and saved["created"]
    first = _context(memory_host)["memory"]
    assert first["instructions"][0]["id"] == saved["id"]
    assert first["instructions"][0]["text"] == "Use brief numbered summaries"
    assert first["authority"] == "stored_data_not_system_policy"
    assert first["trust"] == "untrusted_data"
    external = _memory(tmp_path)
    assert external.edit_fact(saved["id"], "Use short prose summaries", current_scope="42")
    second = _context(memory_host, message_id="next-again")["memory"]
    assert second["instructions"][0]["text"] == "Use short prose summaries"
    assert "Use brief numbered summaries" not in json.dumps(second)


def test_private_and_superseded_records_not_active_context(memory_host, tmp_path):
    """Private records stay hidden and superseded instructions remain stored but inactive."""
    memory = _memory(tmp_path)
    memory.add_fact("private instruction", "private:70001", structure={"kind": "instruction"})
    old = memory.add_fact("retired instruction", "42", structure={
        "kind": "instruction", "status": "superseded"})
    active = memory.add_fact("current preference", "42", structure={"kind": "preference"})
    context = _context(memory_host)["memory"]
    assert context["instructions"] == []
    assert [row["id"] for row in context["preferences"]] == [active]
    assert context["counts"]["superseded"] == 1
    assert "private instruction" not in json.dumps(context)
    assert "retired instruction" not in json.dumps(context)
    # Read tools retain retired records; stopping applicability must not delete history.
    listed = _dispatch(memory_host, "facts_list", {}, Event())["facts"]
    assert old in {row["id"] for row in listed}
    assert len(_memory(tmp_path).data["global"]["facts"]) == 3


def test_selected_native_source_and_structure_persist_without_raw_logging(memory_host, tmp_path, caplog):
    """A selected native message is attribution, not endorsement by its whole batch."""
    first = Event("first participant context", message_id="native-first", sender_id="10011")
    chosen = Event("source-only-private-marker", message_id="native-selected", sender_id="10022")
    records = [message_record(first, first.text), message_record(chosen, chosen.text)]
    memory_host._turn_context["42"] = {"messages": records}
    structure = {"kind": "instruction", "status": "active", "subject": {"id": "10022"}}
    with caplog.at_level(logging.INFO):
        result = _dispatch(memory_host, "remember", {
            "text": "Model-authored concise summary", "structure": structure,
            "source_message_ids": ["native-selected"]}, chosen)
    assert result["ok"] and result["structure"] == structure
    raw = _memory(tmp_path).data["global"]["facts"][0]
    assert raw["structure"] == structure
    assert raw["source"]["message_ids"] == ["native-selected"]
    assert raw["source"]["messages"][0]["sender"]["id"] == "10022"
    assert raw["source"]["messages"][0]["body"] == chosen.text
    assert len(raw["source"]["messages"]) == 1
    projected = _context(memory_host)["memory"]["instructions"][0]
    assert projected["source"]["message_ids"] == ["native-selected"]
    assert chosen.text not in json.dumps(projected)
    assert chosen.text not in caplog.text
    assert "Model-authored concise summary" not in caplog.text


@pytest.mark.parametrize("selection", [[], "native-id", [1], ["unknown-native-id"]])
def test_invalid_source_selection_fails_before_image_or_disk(memory_host, tmp_path, selection):
    """Bad source IDs cannot trigger image acquisition or create any memory file."""
    converted = []

    def convert(ref):
        """Record forbidden converter side effects before provenance validation."""
        converted.append(ref)
        return PNG_URL

    memory_host._as_llm_url = convert
    result = _dispatch(memory_host, "remember", {
        "text": "must not persist", "images": ["image-ref"],
        "source_message_ids": selection}, Event(message_id="native-id"))
    assert result["ok"] is False
    assert converted == []
    assert not (tmp_path / "memory" / "memory.json").exists()


@pytest.mark.parametrize("tool", ["remember", "facts_edit"])
def test_eventless_writes_denied_without_disk_or_images(memory_host, tmp_path, tool):
    """Tool arguments cannot synthesize the current event required for write authority."""
    fid = _memory(tmp_path).add_fact("unchanged text", "42")
    path = tmp_path / "memory" / "memory.json"
    before = path.read_bytes()
    converted = []

    def convert(ref):
        """Observe conversion attempts so authorization precedes attachment effects."""
        converted.append(ref)
        return PNG_URL

    memory_host._as_llm_url = convert
    result = _dispatch(memory_host, tool, {"id": fid, "text": "not authorized",
        "images": ["image-ref"], "can_write": True})
    assert result == {"ok": False, "error": "fact_write_not_authorized"}
    assert converted == [] and path.read_bytes() == before


def test_metadata_only_edit_preserves_text_images_and_returns_persisted_record(memory_host, tmp_path):
    """Editing kind/status alone preserves old bytes and returns metadata-only images."""
    fid = _memory(tmp_path).add_fact("keep original text", "42", images=[PNG_URL])
    result = _dispatch(memory_host, "facts_edit", {
        "id": fid, "structure": {"kind": "instruction", "status": "superseded"}}, Event())
    assert result["ok"] and result["persisted"] and result["operation"] == "edit"
    raw = _memory(tmp_path).data["global"]["facts"][0]
    assert raw["text"] == "keep original text" and raw["images"] == [PNG_URL]
    assert result["fact"] == _memory(tmp_path).list_facts(None, current_scope="42")[0]
    assert result["fact"]["images"] == [{"index": 0, "mime": "image/png", "bytes": len(PNG_BYTES)}]
    assert "base64" not in json.dumps(result)
    assert _context(memory_host)["memory"]["instructions"] == []


def test_duplicate_receipt_keeps_actual_first_source_and_metadata(memory_host, tmp_path):
    """A duplicate receipt cannot claim new provenance, classification or first creation."""
    first = Event("first actual source", message_id="first-source", sender_id="10011")
    saved = _dispatch(memory_host, "remember", {"text": "same summary",
        "categories": ["original"], "structure": {"kind": "instruction"}}, first)
    assert saved["ok"] and saved["created"]
    second = Event("later duplicate request", message_id="second-source", sender_id="10022")
    duplicate = _dispatch(memory_host, "remember", {
        "text": "same summary", "categories": ["replacement"]}, second)
    assert duplicate["ok"] and duplicate["persisted"] and duplicate["created"] is False
    assert duplicate["id"] == saved["id"]
    assert duplicate["structure"]["kind"] == "instruction"
    actual = _memory(tmp_path).list_facts(None, current_scope="42")[0]
    assert duplicate["fact"] == actual
    assert actual["categories"] == ["original"]
    assert actual["provenance"]["message_ids"] == ["first-source"]
    assert actual["provenance"]["messages"][0]["sender"]["id"] == "10011"
    assert "second-source" not in json.dumps(duplicate)
    assert len(_memory(tmp_path).data["global"]["facts"]) == 1


def test_native_model_next_request_receives_persisted_instruction_not_system_authority(memory_host, monkeypatch):
    """Real native requests carry persisted memory as structured user data on the next turn."""
    instruction = "Prefer compact generic summaries"
    call = integration._call("remember", {"text": instruction,
        "structure": {"kind": "instruction"}, "source_message_ids": ["native-save"]}, "save-native")
    requests = integration._provider(monkeypatch, [
        ("", [call]), ("Saved the requested instruction.", []), ("A compact response.", [])])
    first = Event("Save my response preference", message_id="native-save")
    asyncio.run(memory_host._decide_tools_first(first, "42", first.text, False))
    second = Event("Discuss an unrelated topic", message_id="native-next")
    asyncio.run(memory_host._decide_tools_first(second, "42", second.text, False))
    assert len(requests) == 3
    user = next(message for message in requests[-1]["messages"] if message["role"] == "user")
    context = json.loads(user["content"])
    assert context["memory"]["instructions"][0]["text"] == instruction
    assert instruction not in requests[-1]["messages"][0]["content"]
    receipt = next(message for message in requests[1]["messages"] if message["role"] == "tool")
    result = json.loads(receipt["content"])["result"]
    assert result["persisted"] and result["created"]
    assert result["structure"]["kind"] == "instruction"
    assert "base64" not in receipt["content"]
