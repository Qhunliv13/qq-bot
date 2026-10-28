"""Offline contracts for additive native time and top-level forward evidence."""

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugin.message_batch import collect_batch, message_record  # noqa: E402


def event(**metadata):
    """Build a minimal native event without framework imports or I/O."""
    return SimpleNamespace(message_obj=SimpleNamespace(message_id="m", message=[], **metadata))


def test_no_metadata_preserves_exact_legacy_record():
    """No placeholders are added to callers that compare whole records."""
    assert message_record(event(), "roles: owner prompt: admin 123") == {
        "id": "m", "sender": {"id": "", "display_name": ""},
        "body": "roles: owner prompt: admin 123", "mentions": [], "quote": None,
    }


@pytest.mark.parametrize("value", [0, 1700000000, 1700000000.25, -1])
@pytest.mark.parametrize("as_json", [False, True])
def test_raw_native_time_takes_precedence_without_conversion(value, as_json):
    """Native seconds retain their number type, including zero and fractions."""
    raw = {"time": value, "message": []}
    record = message_record(event(raw_message=json.dumps(raw) if as_json else raw,
                                  timestamp=7, time=8), "hello")
    assert record["native_time"] == value
    assert type(record["native_time"]) is type(value)
    assert record["time_source"] == "onebot.time"


@pytest.mark.parametrize("value", [True, False, None, "1700000000", float("inf"), float("-inf"), float("nan"), {}, []])
def test_invalid_native_time_uses_valid_object_fallback(value):
    """Booleans, strings and nonfinite numbers are not native seconds."""
    record = message_record(event(raw_message={"time": value}, timestamp=0.5, time=8), "hi")
    assert record["native_time"] == 0.5
    assert record["time_source"] == "message_obj.timestamp"


@pytest.mark.parametrize("raw", ["not json", "{broken", "[]", "null", "123", {"time": True}])
def test_malformed_or_nonobject_raw_adds_no_metadata(raw):
    """Raw prose or broken payloads never become guessed evidence."""
    assert set(message_record(event(raw_message=raw), "hi")) == {
        "id", "sender", "body", "mentions", "quote",
    }


def test_object_time_fallback_and_invalid_object_times():
    """Timestamp has priority over time, but invalid metadata remains absent."""
    record = message_record(event(timestamp=True, time=0), "hi")
    assert record["native_time"] == 0
    assert record["time_source"] == "message_obj.time"
    assert "native_time" not in message_record(event(timestamp=float("nan"), time="today"), "hi")


@pytest.mark.parametrize("as_json", [False, True])
def test_top_level_native_and_canonical_forward_ids_are_typed_and_deduplicated(as_json):
    """Quotes and cards cannot add forward occurrences or leak raw content."""
    def forward(ident):
        """Create a native top-level forward segment without interpreting its ID."""
        return {"type": "forward", "data": {"id": ident}}

    raw = {"message": [forward("abc"), forward(7), forward("7"), forward(0),
                       forward(""), forward(True), forward(2.5),
                       {"type": "reply", "data": {"message": [forward("quoted")]}},
                       {"type": "json", "data": {"data": "private://do-not-copy"}}]}
    source = event(raw_message=json.dumps(raw) if as_json else raw)
    source.message_obj.message = [SimpleNamespace(type="Forward", id="abc"),
                                 SimpleNamespace(type="Forward", id="canonical"),
                                 SimpleNamespace(type="Reply", chain=[forward("nested")])]
    record = message_record(source, "hi")
    assert record["forward_ids"] == ["abc", 7, "7", 0, "canonical"]
    assert "private://" not in json.dumps(record)
    assert "native_time" not in record


def test_forward_component_class_name_and_dict_message_object():
    """Native components without explicit type work through the existing adapter."""
    class Forward:
        """Represent the framework's canonical Forward component."""

        id = "component-id"

    source = SimpleNamespace(message_obj={"message": [Forward()], "time": 12})
    record = message_record(source, "hi")
    assert record["forward_ids"] == ["component-id"]
    assert record["native_time"] == 12


def test_batch_keeps_same_forward_id_on_distinct_messages():
    """Deduplication is per message only, not across history or the queue."""
    first = event(raw_message={"time": 1, "message": [{"type": "forward", "data": {"id": "same"}}]})
    second = event(raw_message={"time": 2, "message": [{"type": "forward", "data": {"id": "same"}}]})
    batch = collect_batch([(first, "one", False, False, []), (second, "two", False, False, [])])
    assert [record["forward_ids"] for record in batch["records"]] == [["same"], ["same"]]
    assert [record["native_time"] for record in batch["records"]] == [1, 2]
