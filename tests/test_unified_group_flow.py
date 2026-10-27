"""Offline native-provider contracts for one durable public-group timeline.

Reuse the AST-loaded production integration fixture: actual decision/context/tool
methods run, but HTTP responses and all QQ delivery edges remain local fakes.
"""

import asyncio
import copy
import json

import pytest

import test_production_integration as integration
from test_production_integration import Event, _provider
from plugin.cross_scope_memory import CrossScopeMemory, UNIFIED_SOURCE
from plugin.message_batch import message_record

# Alias the existing fixture instead of duplicating the AST production harness.
production = integration.production


def _event(scope="42", text="shared current question", message_id="current-42"):
    """Use native user 1012 metadata rather than identity claims in fixture prose."""
    event = Event(text, message_id=message_id, group=scope)
    event.get_sender_id = lambda: "1012"
    event.get_sender_name = lambda: "Public member"
    return event


def _seed(plugin, scope, text, message_id):
    """Append intact source records through the real recorder, never live caches."""
    event = _event(scope, text, message_id)
    record = message_record(event, text)
    plugin._hist().append(scope, text, record=record)
    return record


def _populate(plugin):
    """Include a muted public source and a private source to distinguish visibility."""
    records = {scope: _seed(plugin, scope, text, ident) for scope, text, ident in (
        ("42", "prior group 42 public-memory nonce", "prior-42"),
        ("77", "prior group 77 public-memory nonce", "prior-77"),
        ("88", "muted group 88 public-memory nonce", "prior-88"),
        ("private:1012", "private hidden-memory nonce", "private-hidden"),
    )}
    plugin._group_policy = lambda group: {"muted": ["88"], "mention_only": []}
    return records


def _request(plugin, event, monkeypatch):
    """Capture the real native HTTP request while SILENT prevents QQ side effects."""
    requests = _provider(monkeypatch, [("SILENT", [])])
    assert asyncio.run(plugin._decide_tools_first(event, event.group, event.text, False)) == ""
    assert len(requests) == 1
    assert event.actions == event.sent == []
    assert [message["role"] for message in requests[0]["messages"]] == ["system", "user"]
    return json.loads(requests[0]["messages"][1]["content"])


def _assert_context(envelope, expected):
    """Require unified provenance/coverage beside history, not split per-group blocks."""
    conversation = envelope["conversation"]
    assert conversation["history"] == expected["records"]
    assert envelope["other_scopes"] == []
    metadata = conversation["history_context"]
    assert metadata["mode"] == "one_shared_timeline"
    assert metadata["source"] == expected["source"] == UNIFIED_SOURCE
    assert metadata["coverage"] == expected["coverage"]
    return conversation


@pytest.mark.parametrize("scope", ["42", "77"])
def test_native_group_request_unifies_public_sources_and_excludes_current(production, monkeypatch, scope):
    """Public current/other/muted history shares one timeline; current stays native once."""
    plugin = production()
    sources = _populate(plugin)
    event = _event(scope, message_id="current-native")
    current = message_record(event, event.text)
    plugin._hist().append(scope, event.text, record=current)
    # IDs belong to their native scope: another group's equal ID is not current.
    collision = _seed(plugin, "88", "same ID but different public source", "current-native")
    plugin._turn_context[scope] = {"history": ["stale local-only history"], "messages": [current]}
    before = {group: plugin._hist().load_records(group, limit=None)
              for group in ("42", "77", "88", "private:1012")}
    expected = CrossScopeMemory(plugin._hist()).unified_snapshot(
        exclude_messages=[(scope, "current-native")])
    envelope = _request(plugin, event, monkeypatch)
    conversation = _assert_context(envelope, expected)
    history = conversation["history"]
    assert {row["origin_scope"] for row in history} == {"42", "77", "88"}
    for group in ("42", "77", "88"):
        assert any(row["record"] == sources[group] for row in history)
    assert any(row["record"] == collision for row in history)
    assert all(not (row["scope"] == scope and row["record"]["id"] == "current-native")
               for row in history)
    assert conversation["messages"][0]["sender"]["id"] == "1012"
    assert conversation["messages"][0]["body"] == event.text
    assert len(conversation["messages"]) == 1
    assert "private hidden-memory nonce" not in json.dumps(envelope)
    assert "stale local-only history" not in json.dumps(envelope)
    assert before == {group: plugin._hist().load_records(group, limit=None) for group in before}


def test_same_query_from_two_groups_has_identical_persisted_history_without_ids(production, monkeypatch):
    """Changing the active group cannot partition a shared stored timeline."""
    plugin = production()
    _populate(plugin)
    captured = []
    for scope in ("42", "77"):
        # No native ID means no guessed exclusion by identical display text.
        event = _event(scope, message_id=None)
        _seed(plugin, scope, event.text, None)
    expected = CrossScopeMemory(plugin._hist()).unified_snapshot()
    for scope in ("42", "77"):
        event = _event(scope, message_id=None)
        envelope = _request(plugin, event, monkeypatch)
        captured.append(_assert_context(envelope, expected)["history"])
    assert captured[0] == captured[1]
    assert sum(row["line"] == "shared current question" for row in captured[0]) == 2


def test_native_batch_is_separate_and_excludes_every_scoped_current_id(production, monkeypatch):
    """Queued native inputs stay ordered once rather than duplicating in prior history."""
    plugin = production()
    _populate(plugin)
    events = [_event("42", "first batch input", "batch-1"),
              _event("42", "second batch input", "batch-2")]
    records = [message_record(event, event.text) for event in events]
    for event, record in zip(events, records):
        plugin._hist().append("42", event.text, record=record)
    plugin._turn_context["42"] = {"messages": copy.deepcopy(records), "history": ["stale batch"]}
    expected = CrossScopeMemory(plugin._hist()).unified_snapshot(
        exclude_messages=[("42", record["id"]) for record in records])
    envelope = _request(plugin, events[-1], monkeypatch)
    conversation = _assert_context(envelope, expected)
    assert [record["id"] for record in conversation["messages"]] == ["batch-1", "batch-2"]
    assert [record["body"] for record in conversation["messages"]] == [event.text for event in events]
    assert not any(row["record"]["id"] in {"batch-1", "batch-2"}
                   for row in conversation["history"])


def test_native_shared_history_uses_240_total_budget_not_twenty_per_group(production, monkeypatch):
    """A large single group retains over twenty rows under the global 240 budget."""
    plugin = production()
    for number in range(245):
        _seed(plugin, "77", f"public history number {number:03d}", f"stored-{number}")
    expected = CrossScopeMemory(plugin._hist()).unified_snapshot()
    conversation = _assert_context(_request(plugin, _event(), monkeypatch), expected)
    assert len(conversation["history"]) == 240
    assert conversation["history"][0]["line"] == "public history number 005"
    assert conversation["history"][-1]["line"] == "public history number 244"
    assert conversation["history_context"]["coverage"]["more_available"] is True
    assert conversation["history_context"]["coverage"]["limit"] == 240


def test_native_timeline_reads_durable_sources_not_group_live_cache(production, monkeypatch):
    """Unpersisted caches and frozen per-group strings cannot override durable evidence."""
    plugin = production()
    _populate(plugin)
    plugin._recent = {"42": ["live current group phantom"], "77": ["live other group phantom"]}
    plugin._turn_context["42"] = {"history": ["frozen phantom"]}
    expected = CrossScopeMemory(plugin._hist()).unified_snapshot()
    envelope = _request(plugin, _event(), monkeypatch)
    _assert_context(envelope, expected)
    assert "phantom" not in json.dumps(envelope)


def test_history_search_default_includes_muted_groups_and_explicit_scope_filters(production):
    """Read permission spans public groups while explicit scope and private isolation remain."""
    plugin = production()
    _populate(plugin)
    event = _event()
    args = {"keyword": "public-memory nonce", "limit": 20}
    result = asyncio.run(plugin._dispatch_tool("history_search", args, "42", event=event))
    assert result["ok"] is True and result["count"] == 3
    assert {row["scope"] for row in result["hits"]} == {"42", "77", "88"}
    filtered = asyncio.run(plugin._dispatch_tool(
        "history_search", dict(args, scope="88"), "42", event=event))
    assert filtered["ok"] is True and filtered["count"] == 1
    assert [row["scope"] for row in filtered["hits"]] == ["88"]
    assert all(row["source"]["trust"] == "untrusted_data" for row in result["hits"])
    assert event.actions == event.sent == []
    muted_event = _event("88")
    assert asyncio.run(plugin._send(muted_event, "88", "must not be delivered")) is None
    assert muted_event.actions == muted_event.sent == []


def test_native_private_turn_keeps_own_history_without_joining_group_timeline(production, monkeypatch):
    """Group pooling must not turn a private conversation into a shared-memory caller."""
    plugin = production()
    _populate(plugin)
    event = _event("private:1012")
    plugin._turn_context[event.group] = {"history": ["own private frozen history"]}
    envelope = _request(plugin, event, monkeypatch)
    assert envelope["conversation"]["history"] == ["own private frozen history"]
    assert "history_context" not in envelope["conversation"]
    assert envelope["other_scopes"] == []
    assert "public-memory nonce" not in json.dumps(envelope)
