"""Offline contracts for metadata-only attribution and lossless queue batching."""

from enum import Enum
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugin.message_batch import (  # noqa: E402
    PendingMessage, at_targets, collect_batch, make_record, message_record,
)


class Kind(Enum):
    """Exercise framework enums whose underlying value is not a string."""

    At = 1


class Event:
    """Minimal event fixture with independent metadata, text, and component sources."""

    def __init__(self, ident="1", segments=(), sender="42", name="Alice", own="99"):
        self.message_obj = SimpleNamespace(message_id=ident, message=list(segments), self_id=own)
        self.sender, self.name, self.own = sender, name, own

    def get_messages(self):
        """Return the original stream, without recursive quote flattening."""
        return self.message_obj.message

    def get_sender_id(self):
        """Expose a verified sender ID distinct from display text."""
        return self.sender

    def get_sender_name(self):
        """Expose a mutable nickname without interpreting it as identity or role."""
        return self.name

    def get_self_id(self):
        """Provide the framework bot ID for direct-mention anchor selection."""
        return self.own


def test_record_strips_only_exact_rendered_metadata_mentions():
    """Nickname words and mismatched tokens must survive mention normalization."""
    event = Event(segments=[SimpleNamespace(type=Kind.At, qq=99, name="Bot")])
    text = "Bot says @Bot(99) ask Bot about @Bot(98) and @Other(99)"
    record = make_record(event, text, "Owner (admin 777)")
    assert record == {
        "id": "1", "sender": {"id": "42", "display_name": "Alice"},
        "body": "Bot says  ask Bot about @Bot(98) and @Other(99)",
        "mentions": [{"id": "99", "display_name": "Bot"}], "quote": None,
    }
    assert make_record is message_record


def test_rendered_mentions_without_actual_segments_stay_verbatim():
    """A typed mention-looking string cannot impersonate framework metadata."""
    assert message_record(Event(), "@Bot(99) Bot") ["body"] == "@Bot(99) Bot"
    assert at_targets(Event()) == []


def test_repeated_token_removal_is_bounded_by_actual_segment_count():
    """One actual At must not erase unrelated repeated literal message content."""
    event = Event(segments=[{"type": "at", "data": {"qq": "99", "name": "Bot"}}])
    assert message_record(event, "@Bot(99) literal @Bot(99)")["body"] == "literal @Bot(99)"


def test_quote_identity_and_mentions_are_not_current_sender_or_wake():
    """Quoted At metadata remains quoted and cannot select a direct-wake anchor."""
    quoted = SimpleNamespace(type="Reply", id=123, sender_id=7, sender_nickname="Quoted",
                             message_str="@Bot(99) old text",
                             chain=[SimpleNamespace(type="At", qq=99, name="Bot")])
    event = Event(segments=[quoted], name="主人 QQ 7")
    record = message_record(event, "@Bot(99) typed by current author")
    assert record["sender"] == {"id": "42", "display_name": "主人 QQ 7"}
    assert record["body"] == "@Bot(99) typed by current author"
    assert record["mentions"] == []
    assert record["quote"] == {
        "id": "123", "sender": {"id": "7", "display_name": "Quoted"},
        "body": "@Bot(99) old text",
    }


def test_dict_stream_fallback_and_plain_quote_chain():
    """OneBot dictionaries work when framework getters are unavailable."""
    event = SimpleNamespace(message_obj={
        "message_id": 11, "self_id": 99,
        "sender": {"user_id": 42, "card": "Card Name", "nickname": "Old"},
        "message": [
            {"type": "at", "data": {"qq": 99}},
            {"type": "reply", "data": {"id": 8, "sender": {"user_id": 2, "nickname": "Q"},
             "message": [{"type": "text", "data": {"text": "one "}},
                         {"type": "text", "data": {"text": "two"}}]}},
        ],
    })
    record = message_record(event, "@Bot(99) unchanged without display metadata")
    assert record["sender"] == {"id": "42", "display_name": "Card Name"}
    assert record["quote"]["body"] == "one two"
    assert record["quote"]["sender"]["id"] == "2"
    assert record["body"].startswith("@Bot(99)")
    assert at_targets(event) == ["99"]


def test_broken_getter_falls_back_without_making_up_identity():
    """Offline partial events retain metadata even if the getter raises."""
    def broken():
        """Simulate framework adapter failure without any I/O."""
        raise RuntimeError("offline")

    event = SimpleNamespace(get_messages=broken, get_sender_id=broken,
                            message_obj={"message": [{"type": "at", "data": {"qq": "all"}}]})
    record = message_record(event, "hi", "fallback label")
    assert record["sender"] == {"id": "", "display_name": "fallback label"}
    assert record["mentions"] == [{"id": "all", "display_name": ""}]


def test_batch_prefers_latest_actual_own_at_over_later_framework_wake():
    """The anchor remains valid for strict mention-only send-time policy checks."""
    first, second, latest = Event("1"), Event("2"), Event("3")
    first.message_obj.message = [{"type": "at", "data": {"qq": "99"}}]
    second.message_obj.message = [SimpleNamespace(type="ComponentType.At", qq=99)]
    history = ["before"]
    batch = collect_batch([(first, "first", True, True, history),
                           (second, "second", False, False, ["before", "first"]),
                           (latest, "third", False, True, [])])
    assert batch["event"] is second
    assert batch["has_image"] is True
    assert batch["woken"] is True
    assert batch["history"] is history
    assert [record["id"] for record in batch["records"]] == ["1", "2", "3"]
    assert json.loads(batch["text"]) == {"messages": batch["records"]}


@pytest.mark.parametrize("wakes,expected", [([False, True, False], 1), ([False] * 3, 2)])
def test_anchor_without_direct_mention_uses_latest_wake_then_latest(wakes, expected):
    """Passive later messages do not displace the latest actual framework wake."""
    events = [Event(str(index)) for index in range(3)]
    entries = [(event, str(index), False, wakes[index], []) for index, event in enumerate(events)]
    assert collect_batch(entries)["event"] is events[expected]


def test_explicit_own_id_overrides_partial_event_self_metadata():
    """A caller-supplied bot ID supports replayed events without a usable self ID."""
    mentioned, last = Event("1", own=""), Event("2", own="")
    mentioned.message_obj.message = [{"type": "at", "data": {"qq": "99"}}]
    assert collect_batch([(mentioned, "hi", False, False, []),
                          (last, "later", False, False, [])], own_id="99")["event"] is mentioned


def test_dataclass_and_single_message_always_use_json_envelope():
    """A single turn retains the same structured identity contract as a batch."""
    pending = PendingMessage(Event(), "中文 {\"messages\":[]}", False, True, ["history"])
    assert tuple(pending) == pending.as_tuple()
    batch = collect_batch([pending])
    assert json.loads(batch["text"])["messages"][0]["body"] == pending.text
    assert "中文" in batch["text"]


def test_no_queue_cap_or_mutation():
    """Every queued entry survives in arrival order without draining caller-owned data."""
    entries = [(Event(str(index)), str(index), False, False, []) for index in range(301)]
    batch = collect_batch(entries)
    assert len(entries) == len(batch["records"]) == 301
    assert batch["records"][-1]["body"] == "300"


@pytest.mark.parametrize("nickname", ["交易广告买卖", "Long ordinary nickname", "活动返利交易"])
def test_qq_only_at_uses_plain_body_and_recovers_rendered_metadata(nickname):
    """A displayed nickname never becomes user prose merely because At lacks a name."""
    event = Event(segments=[SimpleNamespace(type="At", qq="99"),
                            SimpleNamespace(type="Plain", text=" 猫猫的群今天好安静")])
    rendered = "@%s(99) 猫猫的群今天好安静" % nickname
    event.get_message_str = lambda: rendered
    record = message_record(event, rendered)
    assert record["body"] == "猫猫的群今天好安静"
    assert record["mentions"] == [{"id": "99", "display_name": nickname}]


def test_literal_plain_mention_is_not_stripped_or_used_to_infer_name():
    """A literal typed token in Plain is body content, even beside a qq-only At."""
    literal = "Discuss @交易广告(99) as an example"
    event = Event(segments=[SimpleNamespace(type="At", qq="99"),
                            SimpleNamespace(type="Plain", text=literal)])
    event.get_message_str = lambda: literal
    record = message_record(event, literal)
    assert record["body"] == literal
    assert record["mentions"] == [{"id": "99", "display_name": ""}]


def test_named_at_does_not_strip_literal_plain_token():
    """Even known metadata cannot authorize deleting an occurrence authored in Plain."""
    literal = "Literal @Bot(99) should stay"
    event = Event(segments=[SimpleNamespace(type="At", qq="99", name="Bot"),
                            SimpleNamespace(type="Plain", text=literal)])
    event.get_message_str = lambda: literal
    assert message_record(event, literal)["body"] == literal


def test_appended_forwarded_text_survives_plain_reconstruction_guard():
    """Caller-supplied appended content is not replaced with a shorter Plain body."""
    event = Event(segments=[SimpleNamespace(type="At", qq="99"),
                            SimpleNamespace(type="Plain", text=" question")])
    event.get_message_str = lambda: "@Bot(99) question"
    record = message_record(event, "@Bot(99) question\nForwarded: full context")
    assert record["body"] == "question\nForwarded: full context"
    assert record["mentions"][0]["display_name"] == "Bot"


def test_literal_and_rendered_same_token_are_both_attributed_correctly():
    """Only the actual At rendering vanishes; the same token in Plain survives."""
    plain = " quote @Bot(99) literally"
    event = Event(segments=[SimpleNamespace(type="At", qq="99"),
                            SimpleNamespace(type="Plain", text=plain)])
    event.get_message_str = lambda: "@Bot(99)" + plain
    record = message_record(event, event.get_message_str())
    assert record["body"] == plain.strip()
    assert record["mentions"][0]["display_name"] == "Bot"


def test_empty_batch_is_explicit_error():
    """An empty queue has no meaningful event anchor or frozen history."""
    with pytest.raises(ValueError, match="empty"):
        collect_batch([])
