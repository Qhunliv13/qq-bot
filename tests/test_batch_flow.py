"""Offline end-to-end batch contracts against real entry, native loop, and send logic.

Only framework input, HTTP/model output, and explicitly awaited tool I/O are fake;
collection, attribution, history, probability, silence, and consumption remain real.
"""

import asyncio
import copy
import json
import os

import pytest

from test_production_integration import At, Event, Reply, _call, _provider

pytest_plugins = ("test_production_integration",)


def _entry(plugin):
    """Keep unrelated forward and cooldown I/O out of real batch entry execution."""
    async def no_forward(event):
        """Do not acquire forwarded messages in a plain-text batch contract."""
        return ""

    plugin._forward_to_text = no_forward
    plugin._wake_hit = lambda *args: ""
    plugin._rate_ok = lambda *args: True


def _envelope(request):
    """Decode the versioned data envelope without relying on narrative section headers."""
    prompt = request["messages"][1]["content"]
    envelope = json.loads(prompt)
    assert envelope["source"] == {"name": "conversation_context", "trust": "untrusted_data"}
    return envelope["conversation"]


def _sent(events):
    """Count actual confirmed event sends rather than model drafts or queue entries."""
    return [chain for event in events for chain in event.sent]


def _final(text, *message_ids, delivery="new"):
    """Build an explicit final from actual current IDs supplied by each test."""
    return json.dumps({"reply_to": list(message_ids), "text": text,
                       "delivery": delivery}, ensure_ascii=False)


async def _arrive_while_model_active(plugin, events):
    """Freeze the first real model result while later arrivals queue for one follow-up."""
    started, release = asyncio.Event(), asyncio.Event()
    original = plugin._llm_tools
    calls = 0

    async def model(*args, **kwargs):
        """Retain real request construction and hold only the first model I/O boundary."""
        nonlocal calls
        calls += 1
        result = await original(*args, **kwargs)
        if calls == 1:
            started.set()
            await release.wait()
        return result

    plugin._llm_tools = model
    active = asyncio.create_task(plugin.on_message(events[0]))
    try:
        await asyncio.wait_for(started.wait(), 2)
        for event in events[1:]:
            await plugin.on_message(event)
        assert not active.done()
        assert len(plugin._pending["42"]) == len(events) - 1
    finally:
        release.set()
        await asyncio.wait_for(active, 2)


async def _arrive_while_execution_locked(plugin, events):
    """Collect a batch behind the existing execution lock, not a timer window."""
    plugin._execution_lock = asyncio.Lock()
    await plugin._execution_lock.acquire()
    active = asyncio.create_task(plugin.on_message(events[0]))
    try:
        # A scheduler yield lets the first entry queue behind the held lock.
        await asyncio.sleep(0)
        for event in events[1:]:
            await plugin.on_message(event)
        assert len(plugin._pending["42"]) == len(events)
    finally:
        plugin._execution_lock.release()
        await asyncio.wait_for(active, 2)


@pytest.mark.parametrize("answer", ["One reply covers weather and lunch.", "SILENT"])
def test_active_model_collects_later_messages_once_including_whole_batch_silence(production, monkeypatch, answer):
    """First arrival starts immediately; two arrivals during its model form one next batch."""
    plugin = production()
    _entry(plugin)
    events = [Event("weather question", "101"), Event("lunch question", "102"), Event("third subject", "103")]
    finals = [answer if answer == "SILENT" else _final(
        answer, *(event.message_obj.message_id for event in batch))
        for batch in (events[:1], events[1:])]
    requests = _provider(monkeypatch, [(final, []) for final in finals], auto_wrap=False)

    asyncio.run(_arrive_while_model_active(plugin, events))
    assert len(requests) == 2
    assert [record["id"] for record in _envelope(requests[0])["messages"]] == ["101"]
    assert [record["id"] for record in _envelope(requests[1])["messages"]] == ["102", "103"]
    assert len(_sent(events)) == (0 if answer == "SILENT" else 2)
    assert len(plugin._hist().load("42")) == (3 if answer == "SILENT" else 5)
    assert all(event.stopped for event in events)
    assert not any(plugin._pending.values()) and plugin._turn_context == {}
    assert plugin._busy == {"42": False}


def test_arrivals_during_awaited_send_join_next_batch_without_window(production, monkeypatch):
    """Busy state extends through confirmed send; neither sleep nor a window setting is used."""
    plugin = production()
    _entry(plugin)
    first, second, third = Event("first send", "111"), Event("during send two", "112"), Event("during send three", "113")
    requests = _provider(monkeypatch, [(_final("First reply.", first.message_obj.message_id), []),
                                   (_final("Both later messages.", second.message_obj.message_id,
                                           third.message_obj.message_id), [])], auto_wrap=False)
    original_get = os.environ.get
    window_reads, sleeps = [], []

    def environment_get(key, *args):
        """Record forbidden legacy-window reads without changing unrelated environment lookups."""
        if key == "HIGAN_BATCH_WINDOW":
            window_reads.append(key)
            raise AssertionError("batching must not consult a timer setting")
        return original_get(key, *args)

    async def no_sleep(delay, *args, **kwargs):
        """Expose any fixed batching wait, including one swallowed by entry error handling."""
        sleeps.append(delay)
        raise AssertionError("batching must not sleep")

    monkeypatch.setattr(os.environ, "get", environment_get)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    async def scenario():
        """Queue two messages after the first send starts but before it confirms delivery."""
        started, release = asyncio.Event(), asyncio.Event()
        original_send = first.send

        async def blocked_send(chain):
            """Hold the real framework send boundary, not model or handler completion."""
            started.set()
            await release.wait()
            return await original_send(chain)

        first.send = blocked_send
        active = asyncio.create_task(plugin.on_message(first))
        try:
            await asyncio.wait_for(started.wait(), 2)
            await plugin.on_message(second)
            await plugin.on_message(third)
            assert len(requests) == 1 and _sent([first, second, third]) == []
            assert len(plugin._pending["42"]) == 2 and plugin._busy["42"]
            assert not active.done()
        finally:
            release.set()
            await asyncio.wait_for(active, 2)

    asyncio.run(scenario())
    assert window_reads == sleeps == []
    assert len(requests) == len(_sent([first, second, third])) == 2
    assert [record["id"] for record in _envelope(requests[0])["messages"]] == ["111"]
    assert [record["id"] for record in _envelope(requests[1])["messages"]] == ["112", "113"]
    assert not any(plugin._pending.values()) and plugin._turn_context == {}
    assert plugin._busy == {"42": False}


def test_waiting_execution_lock_drains_arrivals_after_lock_not_before(production, monkeypatch):
    """Messages arriving while foreground work holds the lock share one later model turn."""
    plugin = production()
    _entry(plugin)
    events = [Event("first waiting", "201"), Event("second waiting", "202"), Event("third waiting", "203")]
    requests = _provider(monkeypatch, [(_final("All three considered together.",
        *(event.message_obj.message_id for event in events)), [])], auto_wrap=False)

    async def scenario():
        """Hold the real shared execution lock until all three inputs are queued."""
        plugin._execution_lock = asyncio.Lock()
        await plugin._execution_lock.acquire()
        first = asyncio.create_task(plugin.on_message(events[0]))
        try:
            await asyncio.sleep(0)
            assert not first.done() and requests == []
            await plugin.on_message(events[1])
            await plugin.on_message(events[2])
            assert len(plugin._pending["42"]) == 3
        finally:
            plugin._execution_lock.release()
        await asyncio.wait_for(first, 2)

    asyncio.run(scenario())
    assert len(requests) == len(_sent(events)) == 1
    assert [record["id"] for record in _envelope(requests[0])["messages"]] == ["201", "202", "203"]
    assert not any(plugin._pending.values())


def test_metadata_stays_out_of_body_and_multiple_subjects_share_one_reply(production, monkeypatch):
    """Sender/mention commerce-like names are metadata, not invented advertising prose."""
    plugin = production()
    _entry(plugin)
    first = Event("@sale.example(80001) Is the weather clear?", "301", components=[
        At(qq="80001", name="sale.example"),
        Reply(id="old-1", sender_id="70001", sender_nickname="Quoted seller", message_str="quoted offer"),
    ])
    second = Event("What about lunch?", "302")
    first.get_sender_id = lambda: "11001"
    first.get_sender_name = lambda: "discount.example wholesale"
    second.get_sender_id = lambda: "11002"
    second.get_sender_name = lambda: "Lunch speaker"
    requests = _provider(monkeypatch, [(_final("The sky is clear; lunch can be later.",
        first.message_obj.message_id, second.message_obj.message_id), [])], auto_wrap=False)

    # Attribution is a queued-batch contract; preserve the actual parser and lock drain.
    asyncio.run(_arrive_while_execution_locked(plugin, [first, second]))
    assert len(requests) == len(_sent([first, second])) == 1
    records = _envelope(requests[0])["messages"]
    assert [record["body"] for record in records] == ["Is the weather clear?", "What about lunch?"]
    assert records[0]["sender"]["id"] == "11001"
    assert records[0]["sender"]["display_name"] == "discount.example wholesale"
    assert records[1]["sender"]["id"] == "11002"
    assert records[0]["mentions"] == [{"id": "80001", "display_name": "sale.example"}]
    assert records[0]["quote"]["body"] == "quoted offer"
    assert records[0]["quote"]["sender"]["id"] == "70001"
    system = requests[0]["messages"][0]["content"]
    protocol = json.loads(system[len(plugin.persona) + 1:])["protocol"]
    assert protocol["name"] == "conversation_context"
    assert "Display names" in protocol["context"]["identity"]
    assert "discount.example wholesale" not in system and "quoted offer" not in system


@pytest.mark.parametrize("reply_to, quote_id", [
    (["101"], "101"),
    (["102"], None),
    (["102", "101"], "101"),
])
@pytest.mark.parametrize("text", ["answer", "[quote:999]answer"])
def test_structured_batch_selection_reaches_native_send_without_json_or_stale_quotes(
        production, monkeypatch, reply_to, quote_id, text):
    """Real handling sends selected batch attribution, never JSON or embedded/stale quotes."""
    plugin = production()
    _entry(plugin)
    # A previous turn's quote must be replaced even when the latest target needs none.
    plugin._last_quote = "888"
    events = [Event("first subject", "101"), Event("second subject", "102")]
    requests = _provider(monkeypatch, [
        (_final(text, *reply_to), []),
    ], auto_wrap=False)

    # Both records must enter one real _handle_one/_send turn behind the held lock.
    asyncio.run(_arrive_while_execution_locked(plugin, events))
    assert len(requests) == 1
    assert [record["id"] for record in _envelope(requests[0])["messages"]] == ["101", "102"]
    chains = _sent(events)
    assert len(chains) == 1
    components = chains[0].chain
    assert [component.id for component in components if isinstance(component, Reply)] == (
        [quote_id] if quote_id else [])
    # Exact outgoing prose proves neither the structured envelope nor its marker leaks.
    assert [component.text for component in components if hasattr(component, "text")] == ["answer"]
    assert len(components) == (2 if quote_id else 1)
    assert plugin._last_quote == quote_id
    assert plugin._last_reply_selection["reply_to"] == reply_to
    assert plugin._last_reply_selection["structured"] is True
    assert all(event.stopped for event in events)
    assert not any(plugin._pending.values()) and plugin._turn_context == {}
    assert plugin._busy == {"42": False}


def test_probability_is_applied_once_to_collected_batch(production, monkeypatch, tmp_path):
    """Reply percentage is a single batch admission decision, not three per-entry gates."""
    plugin = production()
    _entry(plugin)
    (tmp_path / "groups.json").write_text(json.dumps({"reply_pct": {"42": 50}}), encoding="utf-8")
    draws = []

    def draw():
        """Expose every probability draw while consistently admitting this one batch."""
        draws.append(True)
        return 0.1

    monkeypatch.setattr("random.random", draw)
    events = [Event("unwoken subject %s" % index, str(400 + index)) for index in range(3)]
    requests = _provider(monkeypatch, [(_final("One probabilistic reply.",
        *(event.message_obj.message_id for event in events)), [])], auto_wrap=False)

    # A held execution lock defines this one admission batch without arbitrary waiting.
    asyncio.run(_arrive_while_execution_locked(plugin, events))
    assert len(draws) == len(requests) == len(_sent(events)) == 1
    assert len(_envelope(requests[0])["messages"]) == 3


def test_new_batch_does_not_cancel_active_tool_or_repeat_consumed_messages(production):
    """Active native work completes before two later messages become one separate turn."""
    plugin = production()
    _entry(plugin)
    first, second, third = Event("tool subject", "501"), Event("later weather", "502"), Event("later lunch", "503")
    requests, executed = [], []

    async def scenario():
        """Block an actual native tool boundary while admitting two independent arrivals."""
        started, release = asyncio.Event(), asyncio.Event()

        async def model(system, prompt, refs, messages=None, allow_tools=True):
            """Replace only model I/O and retain real native assistant/tool continuation."""
            requests.append(copy.deepcopy(messages))
            if len(requests) == 1:
                return "", [_call("self_status", call_id="active-tool")]
            if len(requests) == 2:
                assert messages[-1]["role"] == "tool"
                assert messages[-1]["tool_call_id"] == "active-tool"
                return _final("Tool completed.", first.message_obj.message_id), []
            assert len(requests) == 3, "Consumed messages must never trigger another model turn"
            return _final("Weather and lunch together.", second.message_obj.message_id,
                          third.message_obj.message_id), []

        async def tool(name, args, group="", event=None):
            """Model a running effect that must not be cancelled by newly queued chat."""
            executed.append(name)
            started.set()
            await release.wait()
            executed.append("completed")
            return {"ok": True, "nonce": "active-tool-result"}

        plugin._llm_tools, plugin._dispatch_tool = model, tool
        active = asyncio.create_task(plugin.on_message(first))
        try:
            await asyncio.wait_for(started.wait(), 2)
            await plugin.on_message(second)
            await plugin.on_message(third)
            assert not active.done() and executed == ["self_status"]
            assert len(plugin._pending["42"]) == 2
        finally:
            release.set()
        await asyncio.wait_for(active, 2)
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert executed == ["self_status", "completed"]
    assert len(requests) == 3 and len(_sent([first, second, third])) == 2
    first_envelope = _envelope({"messages": requests[0]})
    second_envelope = _envelope({"messages": requests[2]})
    assert [record["id"] for record in first_envelope["messages"]] == ["501"]
    assert [record["id"] for record in second_envelope["messages"]] == ["502", "503"]
    assert len(plugin._tool_evidence().recent("42")) == 1
    assert not any(plugin._pending.values()) and plugin._turn_context == {}
