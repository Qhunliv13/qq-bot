"""Offline end-to-end batch contracts against real entry, native loop, and send logic.

Only framework input, HTTP/model output, and explicitly awaited tool I/O are fake;
collection, attribution, history, probability, silence, and consumption remain real.
"""

import asyncio
import copy
import json

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


@pytest.mark.parametrize("answer", ["One reply covers weather and lunch.", "SILENT"])
def test_short_window_collects_three_messages_once_including_whole_batch_silence(production, monkeypatch, answer):
    """A real short collection window gives three arrivals one model and at most one send."""
    plugin = production()
    _entry(plugin)
    monkeypatch.setenv("HIGAN_BATCH_WINDOW", "0.02")
    events = [Event("weather question", "101"), Event("lunch question", "102"), Event("third subject", "103")]
    requests = _provider(monkeypatch, [(answer, [])])

    async def scenario():
        """Submit concurrently so arrivals land inside the bounded, non-resetting window."""
        await asyncio.wait_for(asyncio.gather(*(plugin.on_message(event) for event in events)), 2)
        # Yield after consumption to expose any accidentally scheduled second response.
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert len(requests) == 1
    assert [record["body"] for record in _envelope(requests[0])["messages"]] == [event.text for event in events]
    assert len(_sent(events)) == (0 if answer == "SILENT" else 1)
    assert len(plugin._hist().load("42")) == (3 if answer == "SILENT" else 4)
    assert all(event.stopped for event in events)
    assert not any(plugin._pending.values()) and plugin._turn_context == {}
    assert plugin._busy == {"42": False}


def test_waiting_execution_lock_drains_arrivals_after_lock_not_before(production, monkeypatch):
    """Messages arriving while foreground work holds the lock share one later model turn."""
    plugin = production()
    _entry(plugin)
    events = [Event("first waiting", "201"), Event("second waiting", "202"), Event("third waiting", "203")]
    requests = _provider(monkeypatch, [("All three considered together.", [])])

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
    monkeypatch.setenv("HIGAN_BATCH_WINDOW", "0.02")
    first = Event("@sale.example(80001) Is the weather clear?", "301", components=[
        At(qq="80001", name="sale.example"),
        Reply(id="old-1", sender_id="70001", sender_nickname="Quoted seller", message_str="quoted offer"),
    ])
    second = Event("What about lunch?", "302")
    first.get_sender_id = lambda: "11001"
    first.get_sender_name = lambda: "discount.example wholesale"
    second.get_sender_id = lambda: "11002"
    second.get_sender_name = lambda: "Lunch speaker"
    requests = _provider(monkeypatch, [("The sky is clear; lunch can be later.", [])])

    async def scenario():
        """Collect two distinct speakers without replacing the actual attribution parser."""
        await asyncio.gather(plugin.on_message(first), plugin.on_message(second))

    asyncio.run(scenario())
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


def test_probability_is_applied_once_to_collected_batch(production, monkeypatch, tmp_path):
    """Reply percentage is a single batch admission decision, not three per-entry gates."""
    plugin = production()
    _entry(plugin)
    monkeypatch.setenv("HIGAN_BATCH_WINDOW", "0.02")
    (tmp_path / "groups.json").write_text(json.dumps({"reply_pct": {"42": 50}}), encoding="utf-8")
    draws = []

    def draw():
        """Expose every probability draw while consistently admitting this one batch."""
        draws.append(True)
        return 0.1

    monkeypatch.setattr("random.random", draw)
    events = [Event("unwoken subject %s" % index, str(400 + index)) for index in range(3)]
    requests = _provider(monkeypatch, [("One probabilistic reply.", [])])

    async def scenario():
        """Feed three unwoken inputs through the actual persisted percentage setting."""
        await asyncio.gather(*(plugin.on_message(event) for event in events))

    asyncio.run(scenario())
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
                return "Tool completed.", []
            assert len(requests) == 3, "Consumed messages must never trigger another model turn"
            return "Weather and lunch together.", []

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
