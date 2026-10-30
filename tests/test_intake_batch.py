"""Length-aware ingress contracts using the real AST-loaded context serializer."""

import copy
import json

import pytest

from test_production_integration import At, Event, Reply
from plugin.context_budget import size
from plugin.conversation_context import system_prompt
from plugin.intake_batch import split_pending
from plugin.message_batch import collect_batch, message_record

pytest_plugins = ("test_production_integration",)


def _limit(monkeypatch, maximum):
    """Override only operator capacity, not production context construction."""
    monkeypatch.setattr("plugin.runtime_settings.load_settings", lambda: {
        "context_memory": {"max_context_chars": maximum}})


def _entries(count, width=1):
    """Retain exact queue tuples and native identity for whole-prefix assertions."""
    return [(Event("%03d:" % index + "x" * width, str(index)),
             "%03d:" % index + "x" * width, False, False, ["old history"])
            for index in range(count)]


def _cost(plugin, entries):
    """Measure production serialization independently while restoring original state."""
    previous = plugin._turn_context
    batch = collect_batch(entries)
    try:
        plugin._turn_context = {"42": {"messages": batch["records"], "history": [],
                                     "clock": plugin._clock_context(), "image_refs": []}}
        prompt = plugin._request_context(batch["event"], "42", batch["text"], batch["has_image"])
        return len(system_prompt(plugin.persona)) + size(plugin._tool_schemas()) + 4096 + len(prompt)
    finally:
        plugin._turn_context = previous


def test_largest_whole_prefix_uses_real_envelope_and_restores_original_turn(production, monkeypatch):
    """Boundary admission retains native evidence and never mutates source or old turn."""
    plugin = production()
    entries = _entries(6, 700)
    event = entries[0][0]
    event.components.extend([At(qq="90001"), Reply(id="quoted", sender_id="123456789", text="quoted body")])
    event.message_obj.raw_message = {"time": 1800000000,
                                    "message": [{"type": "forward", "data": {"id": "native-forward"}}]}
    original = {"history": ["must not be measured" * 1000], "messages": [{"old": True}],
                "_deliveries": [{"delivered": True}]}
    plugin._turn_context["42"] = original
    before = copy.deepcopy(original)
    records = [message_record(item[0], item[1]) for item in entries]
    boundary = _cost(plugin, entries[:3])
    _limit(monkeypatch, boundary)
    contexts = []
    request_context = plugin._request_context

    def capture_context(*args):
        """Observe real serializer copies without replacing production role enrichment."""
        rendered = request_context(*args)
        contexts.append(json.loads(rendered)["conversation"])
        return rendered

    monkeypatch.setattr(plugin, "_request_context", capture_context)
    result = split_pending(plugin, "42", entries)
    assert result["selected"] == entries[:3] and result["remaining"] == entries[3:]
    assert all(actual is expected for actual, expected in zip(result["selected"], entries))
    assert result["fixed_chars"] == boundary and result["max_chars"] == boundary
    assert not result["oversized"]
    assert plugin._turn_context["42"] is original and original == before
    assert [message_record(item[0], item[1]) for item in entries] == records
    # Role enrichment belongs to serialized copies, not canonical source metadata.
    assert "role" not in records[0]["sender"]
    assert records[0]["native_time"] == 1800000000
    assert records[0]["forward_ids"] == ["native-forward"]
    assert all(context["messages"][0]["sender"]["role"] == "member" for context in contexts)
    assert all(context["messages"][0]["quote"]["sender"]["role"] == "owner" for context in contexts)
    assert "must not be measured" not in json.dumps(contexts)


def test_many_tiny_messages_have_no_count_cutoff_and_shrink_with_capacity(production, monkeypatch):
    """A large count fits when short; a smaller character budget changes only prefix."""
    plugin = production()
    entries = _entries(150)
    full = _cost(plugin, entries)
    _limit(monkeypatch, full)
    assert split_pending(plugin, "42", entries)["selected"] == entries
    small = _cost(plugin, entries[:11])
    _limit(monkeypatch, small)
    result = split_pending(plugin, "42", entries)
    assert result["selected"] == entries[:11] and result["remaining"] == entries[11:]
    assert "42" not in plugin._turn_context


def test_first_huge_is_explicit_whole_archive_only_and_later_remains_admissible(production, monkeypatch):
    """An oversized first input must not absorb or permanently block later messages."""
    plugin = production()
    entries = _entries(2, 300)
    huge = Event("H" * 100000, "huge")
    entries.insert(0, (huge, huge.text, False, False, []))
    _limit(monkeypatch, _cost(plugin, entries[1:]) + 10)
    first = split_pending(plugin, "42", entries)
    assert first["oversized"] and first["selected"] == entries[:1]
    assert first["remaining"] == entries[1:] and len(first["selected"][0][1]) == 100000
    second = split_pending(plugin, "42", first["remaining"])
    assert not second["oversized"] and second["selected"] == entries[1:]


@pytest.mark.parametrize("present", [False, True])
def test_serializer_failure_raises_without_loss_and_restores_turn(production, monkeypatch, present):
    """Invalid fixed facts/context must propagate, not masquerade as archive-only input."""
    plugin = production()
    entries = _entries(3)
    original = {"history": ["old"], "fact": {"invalid": True}}
    if present:
        plugin._turn_context["42"] = original

    def invalid_context(*args):
        """Model the production serializer rejecting invalid fixed fact data."""
        raise ValueError("invalid_fixed_facts")

    monkeypatch.setattr(plugin, "_request_context", invalid_context)
    with pytest.raises(ValueError, match="invalid_fixed_facts"):
        split_pending(plugin, "42", entries)
    assert len(entries) == 3
    assert plugin._turn_context.get("42") is (original if present else None)


def test_invalid_budget_raises_before_consuming_entries(production, monkeypatch):
    """Configuration validation must never silently discard any queued input."""
    entries = _entries(3)
    _limit(monkeypatch, "60000")
    with pytest.raises(ValueError):
        split_pending(production(), "42", entries)
    assert [entry[0].message_obj.message_id for entry in entries] == ["0", "1", "2"]
