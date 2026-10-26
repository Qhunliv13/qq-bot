"""Offline provenance contracts using captured Reply.sender_id adapter fields."""

import copy
from enum import Enum
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugin.attachment_sources import collect_sources  # noqa: E402


class Kind(Enum):
    """Exercise canonical framework enums without importing the framework."""

    Image = 1
    Reply = 2


class Event:
    """Keep current identity independent of quote names and forwarded sources."""

    def __init__(self, segments=(), ident="current", sender="member", name="Member"):
        self.message_obj = SimpleNamespace(message_id=ident, message=list(segments))
        self.sender_id = sender
        self.sender_name = name

    def get_messages(self):
        """Return the canonical source stream without flattening quotes."""
        return self.message_obj.message

    def get_sender_id(self):
        """Supply the framework-authoritative current sender ID."""
        return self.sender_id

    def get_sender_name(self):
        """Supply an untrusted display label, never a permission or ownership role."""
        return self.sender_name


def test_current_and_quoted_identical_image_keep_distinct_publishers():
    """A member quoting an owner's image cannot become its publisher or owner."""
    image = SimpleNamespace(type=Kind.Image, url="same", file="unused")
    quote = SimpleNamespace(type=Kind.Reply, id="old", sender_id="owner",
                            sender_nickname="Owner", chain=[image], message_str="489.12")
    event = Event([image, quote])
    event._higan_nontext_context = {"image_refs": ["same"]}
    records = collect_sources(event)
    assert records == [
        {"ref": "same", "source": {"message_id": "current",
         "sender": {"id": "member", "display_name": "Member"}, "relation": "current"}},
        {"ref": "same", "source": {"message_id": "old",
         "sender": {"id": "owner", "display_name": "Owner"}, "relation": "quoted",
         "quoted_message_id": "old", "containing_message_id": "current"}},
    ]
    assert quote.message_str == "489.12"
    assert not any("role" in row["source"]["sender"] for row in records)


def test_unknown_quote_sender_and_message_id_never_inherit_current_identity():
    """Partial quoted metadata retains unknown values instead of caller attribution."""
    event = Event([SimpleNamespace(type="Reply", chain=[
        SimpleNamespace(type="Image", file="quoted.png")])], sender="owner", name="Owner")
    source = collect_sources(event)[0]["source"]
    assert source == {"message_id": "", "sender": {"id": "", "display_name": ""},
                      "relation": "quoted", "quoted_message_id": "",
                      "containing_message_id": "current"}


def test_dict_sources_preserve_reference_priority_and_quote_source_ids():
    """Raw-shaped metadata shares the same existing message identity adapter."""
    event = SimpleNamespace(message_obj={
        "message_id": 10, "sender": {"user_id": 20, "nickname": "Owner claim"},
        "message": [
            {"type": "image", "data": {"url": "https://example.invalid/current",
                                       "file": "ignored"}},
            {"type": "reply", "data": {"id": 11,
             "sender": {"user_id": 21, "nickname": "Quoted"}, "message": [
                 {"type": "image", "data": {"path": "quote.png"}},
                 {"type": "text", "data": {"text": "score 100.01"}}]}},
        ],
    })
    before = copy.deepcopy(event)
    records = collect_sources(event)
    assert [row["ref"] for row in records] == ["https://example.invalid/current", "quote.png"]
    assert records[0]["source"]["sender"] == {"id": "20", "display_name": "Owner claim"}
    assert records[1]["source"]["message_id"] == "11"
    assert records[1]["source"]["sender"]["id"] == "21"
    assert event == before


def test_forwarded_node_identity_is_not_containing_current_sender():
    """Forward origins use supplied node metadata, retaining unknown source IDs."""
    event = Event()
    event._higan_forward_context = {"items": [
        {"sender": {"id": "node", "display_name": "Node"}, "media": [
            {"type": "image", "url": "shared"}, {"type": "video", "url": "ignored"}]},
        {"message_id": "node-message", "sender": None,
         "media": [{"type": "image", "file": "shared"}]},
    ]}
    records = collect_sources(event)
    assert len(records) == 2
    assert records[0]["source"] == {
        "message_id": "", "sender": {"id": "node", "display_name": "Node"},
        "relation": "forwarded", "containing_message_id": "current"}
    assert records[1]["source"]["message_id"] == "node-message"
    assert records[1]["source"]["sender"] == {"id": "", "display_name": ""}


def test_nontext_top_level_refs_deduplicate_only_same_source():
    """Canonical/raw duplicates collapse while a quoted occurrence remains separate."""
    event = Event([SimpleNamespace(type="Image", path="a.png")])
    event._higan_nontext_context = {"image_refs": ["a.png", "sticker", "sticker"]}
    assert [row["ref"] for row in collect_sources(event)] == ["a.png", "sticker"]


def test_no_image_empty_refs_and_quote_prose_do_not_invent_attachments():
    """Neither score text nor card payload URLs may become visual references."""
    event = Event([
        SimpleNamespace(type="Image", url="", file=None),
        SimpleNamespace(type="Reply", id="old", message="https://example.invalid/image"),
        SimpleNamespace(type="json", data={"url": "ignored"}),
    ])
    assert collect_sources(event) == []
    assert collect_sources(Event()) == []


def test_multiple_quotes_keep_each_source_without_a_batchwide_identity():
    """Each actual Reply component supplies its own author and message identifier."""
    event = Event([
        SimpleNamespace(type="Reply", id="q1", sender_id="a", chain=[
            SimpleNamespace(type="Image", url="same")]),
        SimpleNamespace(type="Reply", id="q2", sender_id="b", chain=[
            SimpleNamespace(type="Image", url="same")]),
    ])
    records = collect_sources(event)
    assert [row["source"]["message_id"] for row in records] == ["q1", "q2"]
    assert [row["source"]["sender"]["id"] for row in records] == ["a", "b"]


def test_current_framework_identity_overrides_stale_message_sender():
    """The shared record adapter remains the only current identity authority."""
    event = Event([SimpleNamespace(type="Image", url="image")])
    event.message_obj.sender = {"user_id": "owner", "nickname": "Stale"}
    before = copy.deepcopy(event.__dict__)
    records = collect_sources(event)
    assert records[0]["source"]["sender"] == {"id": "member", "display_name": "Member"}
    assert event.__dict__ == before
