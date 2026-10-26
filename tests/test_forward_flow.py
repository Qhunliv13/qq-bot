"""Offline forward-entry and provider-outage integration contracts.

Reuse the shared AST-loaded production fixture rather than copying production
logic or importing AstrBot startup. All transports and durable stores are local.
"""

import asyncio
import json
from types import SimpleNamespace
import urllib.request

import pytest

from test_production_integration import Event, _call, _provider
from test_production_integration import production as _production_fixture

# Re-export the existing fixture for pytest discovery without copying its loader.
production = _production_fixture


class Forward(SimpleNamespace):
    """Represent AstrBot's direct-attribute Forward component without framework IO."""


def _forward_nodes():
    """Supply five distinct real source records, not a synthesized empty message."""
    # NapCat emits full message envelopes inside get_forward_msg, not minimal nodes.
    return [{"post_type": "message", "message_type": "group", "self_id": 90001,
             "message_id": 71000 + number, "group_id": 42, "time": 1700000000 + number,
             "sender": {"user_id": str(21000 + number), "nickname": f"Source {number}"},
             "message": [{"type": "text", "data": {"text": f"forward-real-text-{number}"}}]}
            for number in range(5)]


def _admit_entry(plugin):
    """Remove only wake/rate policy variability while retaining the real entry and handler."""
    plugin._wake_hit = lambda *args: ""
    plugin._rate_ok = lambda *args: True
    # Admission is an edge policy, not the forward/parser/model behavior under test.
    plugin._policy_allows = lambda *args: True
    plugin._forward_flow_fallback_calls = []

    async def deny_fallback(*args, **kwargs):
        """Record fallback attempts because the real on_message intentionally catches errors."""
        plugin._forward_flow_fallback_calls.append((args, kwargs))
        raise AssertionError("independent _decide fallback is forbidden")

    plugin._decide = deny_fallback


def _provider_outages(monkeypatch, initial=None):
    """Reuse the normal provider envelope before deterministic local transport timeouts."""
    pending = list(initial or [])
    requests = _provider(monkeypatch, pending)
    successful_transport = urllib.request.urlopen

    def transport(request, timeout):
        """Capture failed requests too, proving no hidden independent model attempt occurs."""
        if pending:
            return successful_transport(request, timeout)
        requests.append(json.loads(request.data.decode("utf-8")))
        assert request.full_url == "https://offline.invalid/v1/chat/completions"
        assert timeout > 0
        raise TimeoutError("offline provider timeout")

    monkeypatch.setattr(urllib.request, "urlopen", transport)
    return requests


def _run_chat(plugin, event, entry):
    """Exercise the actual public entry or actual foreground handler without replacement."""
    _admit_entry(plugin)
    if entry == "on_message":
        asyncio.run(plugin.on_message(event))
    else:
        asyncio.run(plugin._handle_one(event, "42", event.text, False, True))


@pytest.mark.parametrize("component_kind", ["direct_id", "raw_id"])
def test_empty_forward_entry_sends_five_real_source_texts_to_provider(production, monkeypatch, component_kind):
    """A textless card resolves via native API before the real message request is assembled."""
    plugin = production()
    component = Forward(id="forward-source") if component_kind == "direct_id" else Forward()
    event = Event(text="", components=[component])
    if component_kind == "raw_id":
        # The framework can discard the component id while preserving raw OneBot data.
        event.message_obj.raw_message = [{"type": "forward", "data": {"id": "forward-source"}}]
    event.action_results["get_forward_msg"] = {"messages": _forward_nodes()}
    requests = _provider(monkeypatch, [("SILENT", [])])

    _run_chat(plugin, event, "on_message")

    assert len(requests) == 1
    envelope = json.loads(requests[0]["messages"][1]["content"])
    records = envelope["conversation"]["messages"]
    assert len(records) == 1
    body = records[0]["body"]
    assert body and "无正文消息" not in body
    forwarded = json.loads(body)
    assert forwarded["source"] == "forwarded_messages"
    assert forwarded["trust"] == "untrusted_data"
    result = forwarded["result"]
    assert result["ok"] and result["recognized"] and result["error"] is None
    assert [item["text"] for item in result["items"]] == [f"forward-real-text-{number}" for number in range(5)]
    assert all(f"forward-real-text-{number}" in body for number in range(5))
    assert event.actions == [("get_forward_msg", {"id": "forward-source"})]
    assert event.stopped and event.sent == []


def test_raw_message_envelope_with_nested_native_forward_preserves_every_leaf(production, monkeypatch):
    """A lossy component recovers the raw card id and expands full native nested envelopes."""
    plugin, event = production(), Event(text="", components=[Forward()])
    event.message_obj.raw_message = {
        "post_type": "message", "message_type": "group", "self_id": 90001,
        "message_id": 1234, "group_id": 42,
        "sender": {"user_id": 10001, "nickname": "Current speaker"},
        "message": [{"type": "forward", "data": {"id": 73001}}]}
    outer, nested = _forward_nodes(), _forward_nodes()[:1]
    nested[0]["message"] = [{"type": "text", "data": {"text": "nested-native-leaf-exact"}}]
    outer[0]["message"].append({"type": "forward", "data": {"id": "nested-native"}})
    responses = {"73001": {"status": "ok", "retcode": 0, "data": {"messages": outer}},
                 "nested-native": {"status": "ok", "retcode": 0, "data": {"messages": nested}}}

    async def native_forward(action, **parameters):
        """Return only the explicit nested native resources, preserving request identity."""
        event.actions.append((action, parameters))
        assert action == "get_forward_msg"
        assert isinstance(parameters["id"], str)
        return responses[parameters["id"]]

    event.bot.api.call_action = native_forward
    requests = _provider(monkeypatch, [("SILENT", [])])
    _run_chat(plugin, event, "on_message")

    assert len(requests) == 1
    envelope = json.loads(requests[0]["messages"][1]["content"])
    body = envelope["conversation"]["messages"][0]["body"]
    forwarded = json.loads(body)
    result = forwarded["result"]
    assert result["recognized"] and result["ok"] and result["error"] is None
    expected = ["forward-real-text-0", "nested-native-leaf-exact", *[
        f"forward-real-text-{number}" for number in range(1, 5)]]
    assert [item["text"] for item in result["items"]] == expected
    assert result["items"][0]["sender"] == {"id": "21000", "display_name": "Source 0"}
    assert result["items"][1]["sender"] == {"id": "21000", "display_name": "Source 0"}
    assert all(text in body for text in expected)
    assert "empty_forward" not in body and "无正文消息" not in body
    assert event.actions == [("get_forward_msg", {"id": "73001"}),
                             ("get_forward_msg", {"id": "nested-native"})]
    assert plugin._forward_flow_fallback_calls == [] and event.sent == []


def test_plain_raw_message_envelope_is_not_promoted_to_forward(production, monkeypatch):
    """Ordinary raw OneBot messages stay ordinary chat despite matching native node metadata."""
    plugin, event = production(), Event(text="ordinary-native-message-exact")
    event.message_obj.raw_message = {
        "post_type": "message", "message_type": "group", "self_id": 90001,
        "message_id": 1234, "sender": {"user_id": 10001, "nickname": "Current speaker"},
        "message": [{"type": "text", "data": {"text": event.text}}]}
    requests = _provider(monkeypatch, [("SILENT", [])])
    _run_chat(plugin, event, "on_message")

    assert len(requests) == 1 and event.actions == []
    envelope = json.loads(requests[0]["messages"][1]["content"])
    assert envelope["conversation"]["messages"][0]["body"] == event.text
    assert "forwarded_messages" not in requests[0]["messages"][1]["content"]
    assert plugin._forward_flow_fallback_calls == [] and event.sent == []


def test_forward_image_metadata_reaches_visual_request_without_losing_source_text(production, monkeypatch):
    """Forward image refs stay event-local and enter the existing bounded image transcript."""
    plugin, event = production(), Event(text="", components=[Forward(id="visual-source")])
    nodes = _forward_nodes()
    urls = [f"https://offline.invalid/forward-image-{number}.png" for number in range(4)]
    nodes[0]["message"].extend({"type": "image", "data": {"url": url}} for url in urls)
    event.action_results["get_forward_msg"] = {"messages": nodes}
    extracted = []

    async def image_refs(source_event, group, text, has_image):
        """Keep acquisition offline while exercising the actual production reference collector."""
        assert source_event is event and group == "42"
        extracted.extend(plugin._image_refs(source_event))
        return list(extracted), ""

    plugin._refs_for = image_refs
    requests = _provider(monkeypatch, [("SILENT", [])])
    _run_chat(plugin, event, "on_message")

    assert len(requests) == 1 and extracted == urls
    content = requests[0]["messages"][1]["content"]
    images = [part["image_url"]["url"] for part in content if part["type"] == "image_url"]
    limit = plugin._decide_tools_first.__globals__["MAX_IMAGES_PER_CALL"]
    assert images == urls[-limit:]
    envelope = json.loads(next(part["text"] for part in content if part["type"] == "text"))
    forwarded = json.loads(envelope["conversation"]["messages"][0]["body"])
    assert len(forwarded["result"]["items"]) == 5
    assert forwarded["result"]["items"][0]["media"] == [{"type": "image", "url": url} for url in urls]
    assert plugin._image_refs(Event()) == []
    assert event.actions == [("get_forward_msg", {"id": "visual-source"})]
    assert event.sent == []


def test_unreadable_forward_entry_preserves_explicit_source_failure(production, monkeypatch):
    """A failed native fetch remains a recognized unreadable source, never an empty poke."""
    plugin, event = production(), Event(text="", components=[Forward(id="unreadable-source")])
    event.action_results["get_forward_msg"] = {"status": "failed", "retcode": 1200,
                                                "message": "offline forward unavailable"}
    requests = _provider(monkeypatch, [("SILENT", [])])

    _run_chat(plugin, event, "on_message")

    assert len(requests) == 1
    envelope = json.loads(requests[0]["messages"][1]["content"])
    body = envelope["conversation"]["messages"][0]["body"]
    assert body and "无正文消息" not in body
    forwarded = json.loads(body)
    result = forwarded["result"]
    assert forwarded["source"] == "forwarded_messages"
    assert forwarded["trust"] == "untrusted_data"
    assert result["recognized"] and result["ok"] is False
    assert result["source"] == "get_forward_msg"
    assert result["items"] == [] and result["text"] == ""
    assert result["error"] == "api_error: offline forward unavailable"
    assert event.actions == [("get_forward_msg", {"id": "unreadable-source"})]
    assert event.sent == []


def test_native_read_forward_and_expand_share_complete_items_without_http_bypass(production, monkeypatch):
    """Both real tool branches use the explicit live event and return identical complete data."""
    plugin, event = production(), Event()
    nodes = _forward_nodes()
    event.action_results["get_forward_msg"] = {"messages": nodes}
    bypass_attempts = []

    def deny_bypass(*args, **kwargs):
        """Make accidental use of a standalone OneBot HTTP client observable and fatal."""
        bypass_attempts.append((args, kwargs))
        raise AssertionError("standalone OneBot HTTP is forbidden")

    monkeypatch.setattr(plugin, "_onebot_http", deny_bypass, raising=False)
    requests = _provider(monkeypatch, [
        ("", [_call("read_forward", {"id": "native-forward"}, "read-source")]),
        ("", [_call("forward_expand", {"id": "native-forward"}, "expand-source")]),
        ("SILENT", [])])

    _run_chat(plugin, event, "_handle_one")

    assert len(requests) == 3 and bypass_attempts == []
    assert event.actions == [("get_forward_msg", {"id": "native-forward"})] * 2
    tool_messages = [message for message in requests[-1]["messages"] if message["role"] == "tool"]
    assert [message["tool_call_id"] for message in tool_messages] == ["read-source", "expand-source"]
    results = [json.loads(message["content"])["result"] for message in tool_messages]
    assert results[0] == results[1]
    assert results[0]["ok"] and len(results[0]["items"]) == 5
    assert [item["text"] for item in results[0]["items"]] == [f"forward-real-text-{number}" for number in range(5)]
    assert [record["result"] for record in plugin._tool_evidence().recent("42")] == results
    assert event.sent == []


@pytest.mark.parametrize("tool_name", ["read_forward", "forward_expand"])
def test_forward_tool_images_follow_paired_source_observation(production, monkeypatch, tool_name):
    """Native forward tools expose source text and image data only after their paired result."""
    plugin, event = production(), Event("read the source forward")
    nodes = _forward_nodes()
    image_url = "https://offline.invalid/native-forward-tool-image.png"
    nodes[0]["message"].append({"type": "image", "data": {"url": image_url}})
    event.action_results["get_forward_msg"] = {"messages": nodes}
    requests = _provider(monkeypatch, [
        ("", [_call(tool_name, {"id": "tool-visual-source"}, "tool-visual-call")]),
        ("SILENT", [])])

    _run_chat(plugin, event, "_handle_one")

    assert len(requests) == 2
    messages = requests[1]["messages"]
    observation_index = next(index for index, message in enumerate(messages) if message["role"] == "tool")
    observation = json.loads(messages[observation_index]["content"])
    assert messages[observation_index]["tool_call_id"] == "tool-visual-call"
    assert observation["result"]["ok"]
    assert [item["text"] for item in observation["result"]["items"]] == [
        f"forward-real-text-{number}" for number in range(5)]
    assert observation["result"]["items"][0]["media"] == [{"type": "image", "url": image_url}]
    visual_messages = [message for message in messages[observation_index + 1:]
                       if isinstance(message.get("content"), list)]
    assert len(visual_messages) == 1 and visual_messages[0]["role"] == "user"
    assert [part["image_url"]["url"] for part in visual_messages[0]["content"]
            if part["type"] == "image_url"] == [image_url]
    assert event.actions == [("get_forward_msg", {"id": "tool-visual-source"})]
    assert plugin._forward_flow_fallback_calls == [] and event.sent == []


@pytest.mark.parametrize("entry", ["_handle_one", "on_message"])
def test_continuous_provider_timeouts_do_not_send_canned_reply_or_fallback(production, monkeypatch, entry):
    """First and forced-final timeouts terminate chat silently after exactly two native calls."""
    plugin, event = production(), Event("offline outage question")
    requests = _provider_outages(monkeypatch)

    _run_chat(plugin, event, entry)

    assert len(requests) == 2
    assert requests[0]["tools"] and "tools" not in requests[1]
    assert "tool_choice" not in requests[1]
    assert requests[1]["messages"][:-1] == requests[0]["messages"]
    assert requests[1]["messages"][-1]["role"] == "system"
    assert plugin._forward_flow_fallback_calls == []
    assert event.sent == [] and plugin._last_reply_at == {} and plugin._last_sent == {}
    assert not any(line.startswith("彼岸酱：") for line in plugin._hist().load("42"))
    assert plugin._tool_evidence().recent("42") == []


@pytest.mark.parametrize("entry", ["_handle_one", "on_message"])
def test_successful_tool_is_not_replayed_when_all_final_provider_attempts_timeout(production, monkeypatch, entry):
    """One executed native tool remains durable even when subsequent completion never succeeds."""
    plugin, event = production(), Event("read files before provider outage")
    event.action_results["get_group_root_files"] = {"files": [{
        "file_name": "persisted-before-outage.txt", "file_id": "source-1", "file_size": 7}], "folders": []}
    requests = _provider_outages(monkeypatch, [
        ("", [_call("group_files", {"limit": 20}, "executed-before-outage")])])

    _run_chat(plugin, event, entry)

    assert len(requests) == 3
    assert event.actions == [("get_group_root_files", {"group_id": 42, "file_count": 20})]
    assert requests[1]["tools"] and "tools" not in requests[2]
    assert "tool_choice" not in requests[2]
    tool_messages = [message for message in requests[2]["messages"] if message["role"] == "tool"]
    assert len(tool_messages) == 1 and tool_messages[0]["tool_call_id"] == "executed-before-outage"
    observation = json.loads(tool_messages[0]["content"])
    assert observation["result"]["ok"]
    assert observation["result"]["files"][0]["name"] == "persisted-before-outage.txt"
    restored = production()
    evidence = restored._tool_evidence().recent("42")
    assert len(evidence) == 1 and evidence[0]["result"] == observation["result"]
    assert plugin._forward_flow_fallback_calls == []
    assert event.sent == [] and plugin._last_reply_at == {} and plugin._last_sent == {}
    assert not any(line.startswith("彼岸酱：") for line in plugin._hist().load("42"))


def test_task_provider_timeouts_raise_instead_of_claiming_success(production, monkeypatch):
    """The real task entry propagates failed finalization rather than returning a fake result."""
    plugin = production()
    _admit_entry(plugin)
    requests = _provider_outages(monkeypatch)
    task = {"id": "offline-task", "scope": "42", "origin": "offline:42", "goal": "offline task"}

    with pytest.raises(RuntimeError, match="^task_finalization_failed$"):
        asyncio.run(plugin._run_task_once(task))

    assert len(requests) == 2 and "tools" not in requests[-1]
    assert plugin._forward_flow_fallback_calls == []
    assert plugin._hist().load("42") == [] and plugin._last_sent == {}


def test_task_scheduler_marks_finalization_outage_failed_not_success(production, monkeypatch, tmp_path):
    """The actual scheduler persists a paused failed outcome after the two unavailable calls."""
    from plugin import tasks

    plugin = production()
    _admit_entry(plugin)
    monkeypatch.setattr(tasks, "ROOT", str(tmp_path / "tasks"))
    monkeypatch.setattr(tasks, "STORE", str(tmp_path / "tasks" / "tasks.json"))
    plugin._tasks_mod = lambda: tasks
    task = tasks.add("offline finalization", "No successful completion is available", "42",
                     repeat="once", origin="offline:42")
    requests = _provider_outages(monkeypatch)
    ticks = []

    async def scheduler_tick(seconds):
        """Run exactly one real scheduler iteration without waiting or changing its logic."""
        ticks.append(seconds)
        if len(ticks) > 1:
            plugin._stopping = True

    monkeypatch.setattr(asyncio, "sleep", scheduler_tick)
    asyncio.run(plugin._task_loop())

    assert len(requests) == 2 and len(ticks) == 2
    stored = next(row for row in tasks.listing(only_active=False) if row["id"] == task["id"])
    assert stored["status"] == "paused"
    assert stored["note"] == "RuntimeError"
    assert stored["execution_state"] == "failed"
    assert stored["nudges"] == 1 and "claim_id" not in stored
    assert plugin._forward_flow_fallback_calls == []
    assert plugin._last_sent == {} and plugin._hist().load("42") == []
