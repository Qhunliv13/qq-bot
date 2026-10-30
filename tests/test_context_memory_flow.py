"""Actual native context compaction/provider flow, with only HTTP and QQ mocked."""
import asyncio
import io
import json
import urllib.request

from plugin.context_budget import size
from plugin.cross_scope_memory import CrossScopeMemory
from plugin.message_batch import message_record
from test_production_integration import Event, _provider

pytest_plugins = ("test_production_integration",)


def _seed(plugin, rows=14):
    """Persist authentic typed source rows too long for a small context region."""
    for index in range(rows):
        event = Event("A" * 1000 + str(index), message_id=str(5000 + index))
        record = message_record(event, event.text)
        plugin._hist().append("42", event.text, record=record)


def _budget(monkeypatch):
    """Reserve enough fixed schemas but force raw history to exceed remaining length."""
    monkeypatch.setattr("plugin.context_budget.limits", lambda: {
        "max_context_chars": 60000, "summary_chars": 24000})


def test_native_compaction_is_saved_and_effectively_sent_next_request(production, monkeypatch):
    """A real model-request boundary creates, saves, uses and rehydrates its summary."""
    plugin = production()
    _budget(monkeypatch)
    _seed(plugin)
    archived = plugin._hist().load_records("42", limit=None)
    source_rows = CrossScopeMemory(plugin._hist()).unified_snapshot(max_chars=60000)["records"]
    # One summary provider turn and one ordinary model decision. No fake human
    # summary message or tool action is injected into the conversation archive.
    requests = []

    def urlopen(request, timeout):
        """Script the provider by request type, not by an assumed number of chunks."""
        payload = json.loads(request.data.decode("utf-8"))
        requests.append(payload)
        assert timeout > 0
        data = json.loads(payload["messages"][1]["content"])
        text = "SILENT" if "conversation" in data else "记得之前的讨论和未完成事项。"
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": text}}]}).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    event = Event("current question")
    plugin._turn_context["42"] = {"messages": [message_record(event, event.text)]}
    assert asyncio.run(plugin._decide_tools_first(event, "42", event.text, False)) == ""
    assert len(requests) >= 2
    summary_request = requests[0]
    assert "tools" not in summary_request
    summary_input = json.loads(summary_request["messages"][1]["content"])
    assert summary_input["scope"] == "42"
    assert summary_input["records"]
    final_request = requests[-1]
    context = json.loads(final_request["messages"][1]["content"])
    summary = context["memory"]["context_summary"]
    assert summary["generated"] and summary["status"] == "updated"
    assert summary["summaries"][0]["text"] == "记得之前的讨论和未完成事项。"
    assert summary["summaries"][0]["trust"] == "untrusted_data"
    history = context["conversation"]["history"]
    coverage = context["conversation"]["history_context"]["coverage"]
    regions = coverage["allocation"]
    available = max(0, 60000 - coverage["fixed_chars"] - 2048)
    assert regions["available_chars"] == available
    assert regions["raw_chars"] == available * 3 // 5 == coverage["max_chars"]
    assert regions["summary_chars"] == available - regions["raw_chars"]
    assert regions["raw_fraction"] == 0.6 and regions["summary_archive_fraction"] == 0.4
    assert 0 < len(history) < 14
    assert 0 < size(history) <= regions["raw_chars"]
    assert 0 < size(summary["summaries"]) <= regions["summary_chars"]
    assert coverage["source_chars"] == sum(size(row) for row in source_rows)
    assert coverage["retained_source_chars"] == sum(size(row) for row in history)
    assert coverage["omitted_source_chars"] == coverage["source_chars"] - coverage["retained_source_chars"] > 0
    assert summary["archived_source_records"] == summary["summarized_source_records"] == coverage["omitted"]
    assert summary["archived_source_chars"] == summary["summarized_source_chars"] == coverage["omitted_source_chars"]
    assert summary["archive_only_records"] == summary["archive_only_chars"] == 0
    assert summary["archive_deleted_records"] == coverage["archive_deleted_records"] == 0
    assert summary["summaries"][0]["lossy"] is True
    assert context["conversation"]["messages"][0]["id"] == "1234"
    assert plugin._hist().load_records("42", limit=None) == archived
    assert len(plugin._hist().load_records("42", limit=None)) == 14
    assert event.actions == event.sent == []
    fresh = production()
    _budget(monkeypatch)
    later = _provider(monkeypatch, [("SILENT", [])], auto_wrap=False)
    fresh._turn_context["42"] = {"messages": [message_record(event, event.text)]}
    assert asyncio.run(fresh._decide_tools_first(event, "42", event.text, False)) == ""
    assert len(later) == 1, "A persisted unchanged prefix must not trigger another summary model call"
    loaded = json.loads(later[0]["messages"][1]["content"])["memory"]["context_summary"]
    assert loaded["status"] == "loaded" and not loaded["generated"]
    assert loaded["summaries"][0]["text"] == summary["summaries"][0]["text"]


def test_many_current_inputs_remain_complete_when_history_is_compacted(production, monkeypatch):
    """A native burst stays intact once; compaction only changes the historical view."""
    plugin = production()
    # Current native inputs are irreducible; reserve their real serialized size
    # while keeping a sufficiently long historical prefix to require compaction.
    monkeypatch.setattr("plugin.context_budget.limits", lambda: {
        "max_context_chars": 150000, "summary_chars": 60000})
    _seed(plugin, rows=80)
    events = [Event("burst input " + str(index), message_id="burst-" + str(index))
              for index in range(80)]
    records = [message_record(event, event.text) for event in events]
    for record in records:
        record["sender"]["role"] = "member"
    for event, record in zip(events, records):
        plugin._hist().append("42", event.text, record=record)
    archived = plugin._hist().load_records("42", limit=None)
    plugin._turn_context["42"] = {"messages": records}
    requests = []

    def urlopen(request, timeout):
        """Respond by native request shape so any required summary chunk may run."""
        payload = json.loads(request.data.decode("utf-8"))
        requests.append(payload)
        assert timeout > 0
        data = json.loads(payload["messages"][1]["content"])
        text = "SILENT" if "conversation" in data else "Earlier history compacted."
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": text}}]}).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    event = events[-1]
    assert asyncio.run(plugin._decide_tools_first(event, "42", event.text, False)) == ""
    assert len(requests) >= 2
    envelope = json.loads(requests[-1]["messages"][1]["content"])
    conversation = envelope["conversation"]
    assert conversation["messages"] == records
    assert len({record["id"] for record in conversation["messages"]}) == 80
    current_ids = {record["id"] for record in records}
    assert all(row["record"]["id"] not in current_ids for row in conversation["history"])
    coverage = conversation["history_context"]["coverage"]
    summary = envelope["memory"]["context_summary"]
    assert coverage["omitted"] > 0 and summary["generated"]
    assert 0 < size(conversation["history"]) <= coverage["allocation"]["raw_chars"]
    assert 0 < size(summary["summaries"]) <= coverage["allocation"]["summary_chars"]
    assert coverage["archive_deleted_records"] == summary["archive_deleted_records"] == 0
    assert plugin._hist().load_records("42", limit=None) == archived
    assert len(plugin._hist().load_records("42", limit=None)) == 80 + 80
    assert all(item.actions == item.sent == [] for item in events)


def test_current_batch_too_large_stops_provider_but_preserves_archive(production, monkeypatch):
    """An irreducible burst is not silently processed or deleted to fake a budget fit."""
    plugin = production()
    _budget(monkeypatch)
    events = [Event("large input " + str(index) + "x" * 1000,
                    message_id="oversized-" + str(index)) for index in range(80)]
    records = [message_record(event, event.text) for event in events]
    for event, record in zip(events, records):
        plugin._hist().append("42", event.text, record=record)
    archived = plugin._hist().load_records("42", limit=None)
    plugin._turn_context["42"] = {"messages": records}
    requests = _provider(monkeypatch, [], auto_wrap=False)
    assert asyncio.run(plugin._decide_tools_first(events[-1], "42", events[-1].text, False)) == ""
    assert requests == []
    assert len(plugin._turn_context["42"]["messages"]) == 80
    assert plugin._hist().load_records("42", limit=None) == archived
    assert all(event.actions == event.sent == [] for event in events)


def test_native_summary_failure_preserves_archive_and_exposes_status(production, monkeypatch):
    """No unusable summary is published or saved as a successful compression."""
    plugin = production()
    _budget(monkeypatch)
    _seed(plugin)
    requests = _provider(monkeypatch, [("", []), ("SILENT", [])], auto_wrap=False)
    event = Event()
    assert asyncio.run(plugin._decide_tools_first(event, "42", event.text, False)) == ""
    context = json.loads(requests[-1]["messages"][1]["content"])
    summary = context["memory"]["context_summary"]
    assert summary["status"] == "summary_failed"
    assert summary["summaries"] == [] and not summary["generated"]
    assert len(plugin._hist().load_records("42", limit=None)) == 14
    assert event.actions == event.sent == []
