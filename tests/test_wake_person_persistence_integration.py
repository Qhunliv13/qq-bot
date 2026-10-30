"""Focused production-entry wake-person persistence tests without framework services."""

import asyncio
import json

import pytest

import test_production_integration as integration
from plugin.memory import WakePersonFacts

production = integration.production
Event = integration.Event


@pytest.fixture
def wake_host(production, tmp_path, monkeypatch):
    """Bind the real production dispatch/intake to a synthetic configured person root."""
    host = production()
    monkeypatch.setitem(host._dispatch_tool.__func__.__globals__, "HIGAN_ROOT", str(tmp_path))
    config = {"wake_persons": {"42": ["10001"]},
              "wake_person_conf": {"10001": {"pct": 0, "hours": [0, 0]}}}
    (tmp_path / "groups.json").write_text(json.dumps(config), encoding="utf-8")
    return host


def _dispatch(host, name, args, event=None):
    """Exercise the actual main.py branch rather than only its pure helper."""
    return asyncio.run(host._dispatch_tool(name, args, "42", event=event))


def _records(tmp_path):
    """Reload observations/facts after each call, simulating a fresh plugin instance."""
    return WakePersonFacts(str(tmp_path / "memory" / "wake_persons.json")).load().list(
        "10001", current_scope="42")


def test_no_reply_admission_still_saves_native_wake_person_message(wake_host, tmp_path):
    """Person ingestion precedes probability and reply policy without needing an LLM."""
    # Deliberately stop after the actual probability block: neither a tool/model
    # call nor admitted reply may be necessary for durable native observations.
    wake_host._policy_allows = lambda event, group: False
    wake_host._wake_hit = lambda event, text, group: "person"
    wake_host._person_conf = lambda qq: {"pct": 0, "hours": [], "words": []}
    wake_host._hours_ok = lambda hours: True
    event = Event("observed, but no reply", message_id="native-no-reply")
    asyncio.run(wake_host.on_message(event))
    assert event.stopped and event.sent == []
    rows = _records(tmp_path)
    assert len(rows) == 1 and rows[0]["kind"] == "observed_message"
    assert rows[0]["text"] == event.text
    assert rows[0]["source"]["message_ids"] == ["native-no-reply"]
    asyncio.run(wake_host.on_message(event))
    assert len(_records(tmp_path)) == 1


def test_production_dispatch_model_fact_success_and_eventless_failure(wake_host, tmp_path):
    """The real tool branch persists checked model facts and denies eventless mutation."""
    event = Event("please remember", message_id="tool-request")
    result = _dispatch(wake_host, "wp_fact_add", {"qq": "10001", "text": "model-written fact"}, event)
    assert result["ok"] and result["persisted"] and result["kind"] == "model_fact"
    assert _records(tmp_path)[0]["source"]["message_ids"] == ["tool-request"]
    listed = _dispatch(wake_host, "wp_fact_list", {"qq": "10001"}, event)
    assert listed["ok"] and listed["facts"][0]["id"] == result["id"]
    denied = _dispatch(wake_host, "wp_fact_add", {"qq": "10001", "text": "denied"})
    assert denied == {"ok": False, "error": "wake_person_write_not_authorized"}
    assert len(_records(tmp_path)) == 1


def test_production_corruption_read_and_write_fail_without_replacing(wake_host, tmp_path):
    """Corruption returns explicit unavailable, not empty successful reads or writes."""
    path = tmp_path / "memory" / "wake_persons.json"
    path.parent.mkdir(parents=True)
    path.write_text("{corrupt", encoding="utf-8")
    for name, args in [("wake_person_info", {"qq": "10001"}),
                       ("wp_fact_list", {"qq": "10001"}),
                       ("wp_fact_add", {"qq": "10001", "text": "do not overwrite"})]:
        assert _dispatch(wake_host, name, args, Event()) == {
            "ok": False, "error": "wake_person_store_unavailable"}
    assert path.read_text(encoding="utf-8") == "{corrupt"


def test_native_other_speaker_and_notice_do_not_ingest(wake_host, tmp_path):
    """Only actual wake-person utterances enter the person store, never notice sources."""
    wake_host._policy_allows = lambda event, group: False
    other = Event("not a wake person", message_id="other", sender_id="10002")
    asyncio.run(wake_host.on_message(other))
    notice = Event("native notice metadata", message_id="notice", raw_message={"post_type": "notice"})
    asyncio.run(wake_host.on_message(notice))
    assert _records(tmp_path) == []
    assert not (tmp_path / "memory" / "wake_persons.json").exists()
