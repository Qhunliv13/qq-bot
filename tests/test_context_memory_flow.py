"""Actual native context compaction/provider flow, with only HTTP and QQ mocked."""
import asyncio
import io
import json
import urllib.request

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
        "max_context_chars": 60000, "summary_chars": 7500})


def test_native_compaction_is_saved_and_effectively_sent_next_request(production, monkeypatch):
    """A real model-request boundary creates, saves, uses and rehydrates its summary."""
    plugin = production()
    _budget(monkeypatch)
    _seed(plugin)
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
    assert len(context["conversation"]["history"]) < 14
    assert context["conversation"]["messages"][0]["id"] == "1234"
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
