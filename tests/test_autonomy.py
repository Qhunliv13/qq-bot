"""Offline opportunity scheduling tests with isolated files and explicit clock data."""
from datetime import datetime
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def module():
    """Load the pure controller without importing the AstrBot plugin runtime."""
    path = Path(__file__).resolve().parents[1] / "plugin" / "autonomy.py"
    spec = importlib.util.spec_from_file_location("isolated_autonomy", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def controller(module, tmp_path):
    """Pin observation time independently from each tested opportunity time."""
    return module.AutonomousController(tmp_path / "autonomy.json", now=lambda: 1000)


def timestamp(module, day, clock="09:00"):
    """Construct an explicit UTC+8 calendar time without depending on host timezone."""
    return datetime.fromisoformat(day + "T" + clock).replace(tzinfo=module.LOCAL_TIMEZONE).timestamp()


def test_defaults_do_not_create_file(controller):
    """Merely loading defaults must not create or enable a background task."""
    state = controller.snapshot()
    assert state["enabled"] is True and state["interval_seconds"] == 600
    assert state["pending_events"] == {} and state["notifications_seen"] == []
    assert not controller.path.exists()
    assert controller.opportunities({}, now=2000) == []


def test_observation_is_authentic_and_idempotent(controller):
    """Absent origins are not invented and identical actual observations do not rewrite."""
    assert not controller.observe("42", "")
    assert not controller.path.exists()
    assert controller.observe("42", "onebot:GroupMessage:42")
    before = controller.path.read_bytes()
    assert not controller.observe("42", "onebot:GroupMessage:42")
    assert controller.path.read_bytes() == before
    assert controller.origin("42") == "onebot:GroupMessage:42"
    assert controller.origin("43") == ""


def test_first_heartbeat_waits_from_observation(controller):
    """The default cadence begins when a genuine destination is first observed."""
    controller.observe("42", "actual-origin")
    assert controller.opportunities({}, now=1599) == []
    event, = controller.opportunities({}, now=1600)
    assert event["kind"] == "heartbeat" and event["scope"] == "42"
    assert event["origin"] == "actual-origin"
    assert event["due_at"] == 1600
    assert "messages" not in event and "text" not in event


def test_heartbeat_round_robin_and_restart(module, controller):
    """One global heartbeat selects the next observed scope durably, not every friend."""
    controller.observe("42", "origin42")
    controller.observe("43", "origin43")
    event, = controller.opportunities({}, now=1600)
    assert controller.ack(event, now=1600)
    assert not controller.ack(event, now=1600)
    reloaded = module.AutonomousController(controller.path)
    assert reloaded.opportunities({}, now=2199) == []
    following, = reloaded.opportunities({}, now=2200)
    assert following["scope"] == "43"
    assert reloaded.snapshot()["last_started"]["outcome"] == "started"


def test_mentions_and_muted_remain_restricted(controller):
    """No trigger mode quietly overrides explicitly saved mention-only or muted policy."""
    controller.observe("457422816", "actual-origin")
    conf = {"mention_only": ["457422816"], "poll_minutes": {"457422816": 1},
            "wake_at": {"457422816": ["08:26"]}}
    controller.schedule("457422816", next_at=1000)
    assert controller.opportunities(conf, now=1600) == []
    conf = {"muted": ["457422816"], "poll_minutes": {"457422816": 1}}
    assert controller.opportunities(conf, now=1600) == []
    assert controller.origin("457422816") == "actual-origin"


def test_explicit_poll_without_origin_is_read_only_data(controller):
    """A configured scope may be inspected but is never assigned a synthetic origin."""
    event, = controller.opportunities({"poll_minutes": {"42": 2}}, now=1000)
    assert event["kind"] == "poll_interval" and event["origin"] == ""
    assert not controller.path.exists()
    assert controller.ack(event, now=1000)
    assert controller.opportunities({"poll_minutes": {"42": 2}}, now=1119) == []
    again, = controller.opportunities({"poll_minutes": {"42": 2}}, now=1120)
    assert again["due_at"] == 1120
    assert not controller.ack(event, now=1120)


def test_wake_cross_day_and_restart(module, controller):
    """A wall-clock slot fires once each local day, including after restart."""
    conf = {"wake_at": {"42": ["09:00"]}}
    first = timestamp(module, "2026-10-25")
    event, = controller.opportunities(conf, now=first)
    assert controller.ack(event, now=first)
    restarted = module.AutonomousController(controller.path)
    assert restarted.opportunities(conf, now=first + 30) == []
    tomorrow, = restarted.opportunities(conf, now=first + 86400)
    assert tomorrow["event_id"] != event["event_id"]
    assert restarted.ack(tomorrow, now=first + 86400)
    assert not restarted.ack(event, now=first + 86400)
    assert restarted.snapshot()["wake_slots"] == {"42@09:00": "2026-10-26"}


def test_reading_opportunities_does_not_consume(controller):
    """A busy foreground lock can defer without silently losing a due event."""
    conf = {"poll_minutes": {"42": 1}}
    assert controller.opportunities(conf, now=1000) == controller.opportunities(conf, now=1000)
    assert not controller.path.exists()


def test_disabled_heartbeat_preserves_explicit_poll(controller):
    """Global free deliberation and a user's existing explicit poll are distinct settings."""
    controller.observe("42", "origin")
    controller.schedule(enabled=False)
    events = controller.opportunities({"poll_minutes": {"42": 1}}, now=1600)
    assert [event["kind"] for event in events] == ["poll_interval"]


def test_model_can_schedule_next_check_and_edit_cadence(controller):
    """Small positive intervals and explicit next checks are not given arbitrary caps."""
    controller.observe("42", "origin")
    controller.schedule("42", next_at=1000.25, interval_seconds=0.5, reason="read later")
    event, = controller.opportunities({}, now=1000.25)
    assert event["kind"] == "model_schedule"
    assert controller.ack(event, now=1000.25)
    assert controller.snapshot()["next_checks"] == {}
    assert controller.snapshot()["interval_seconds"] == 0.5
    controller.schedule(next_at=9000)
    assert controller.opportunities({}, now=8999) == []
    event, = controller.opportunities({}, now=9000)
    assert event["kind"] == "heartbeat"


def test_configured_scopes_do_not_create_origins(controller):
    """Selecting a scope is not evidence of a real authenticated framework destination."""
    controller.observe("42", "origin")
    controller.schedule(scopes=["43"])
    assert controller.opportunities({}, now=2000) == []


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True])
def test_invalid_intervals_rejected_without_write(controller, value):
    """Reject invalid timing data without silently normalizing or persisting it."""
    with pytest.raises(ValueError):
        controller.schedule(interval_seconds=value)
    assert not controller.path.exists()


@pytest.mark.parametrize("scope", ["", "0", "private:0", "unknown", "../42"])
def test_invalid_destinations_rejected(controller, scope):
    """Generic destination parsing has no fallback group or path interpretation."""
    with pytest.raises(ValueError):
        controller.observe(scope, "origin")


def test_corrupt_state_does_not_reset_enabled(controller):
    """Invalid existing state fails closed rather than reviving default free activity."""
    controller.path.write_text(json.dumps({"enabled": "yes"}), encoding="utf-8")
    with pytest.raises(ValueError):
        controller.opportunities({}, now=2000)


def test_result_arrival_at_five_minutes_does_not_wait_for_round_robin(controller):
    """A completed worker offers data immediately, not after the thirty-minute tick."""
    controller.observe("42", "origin42")
    controller.observe("43", "origin43")
    controller.schedule(interval_seconds=1800)
    assert controller.opportunities({}, now=1299) == []
    assert controller.notify("043", "worker-receipt", now=1300)
    before = controller.path.read_bytes()
    event, = controller.opportunities({}, now=1300)
    assert event == {"kind": "activity_result", "scope": "43",
                     "source_type": "delegated_result", "event_id": "activity:worker-receipt",
                     "observed_at": 1300, "due_at": 1300, "origin": "origin43"}
    assert controller.opportunities({}, now=1301) == [event]
    assert controller.path.read_bytes() == before
    assert not {"messages", "text", "force_send", "human"}.intersection(event)
    assert controller.ack(event, now=1301)
    assert controller.opportunities({}, now=2799) == []
    heartbeat, = controller.opportunities({}, now=2800)
    assert heartbeat["scope"] == "42" and heartbeat["kind"] == "heartbeat"


def test_notify_requires_observed_origin_and_does_not_write_unknown_scope(controller):
    """Notifications cannot fabricate an authentic destination from a completion id."""
    assert not controller.notify("42", "receipt", now=1001)
    assert not controller.path.exists()
    controller.observe("42", "real-group-origin")
    assert not controller.notify("private:42", "receipt", now=1001)
    assert controller.notify("00042", "receipt", now=1001)
    assert controller.snapshot()["pending_events"] == {
        "receipt": {"scope": "42", "kind": "delegated_result", "observed_at": 1001}}


@pytest.mark.parametrize("identity", ["", "  ", 42, None, True, "x" * 201])
def test_notify_rejects_invalid_receipt_ids_without_write(controller, identity):
    """Opaque ids must be nonempty bounded strings, never coerced or truncated."""
    with pytest.raises(ValueError):
        controller.notify("42", identity)
    assert not controller.path.exists()


@pytest.mark.parametrize("kind", ["tool_result", "worker_complete", "human", None, [], 1])
def test_notify_rejects_unregistered_source_types(controller, kind):
    """Only the actual registered delegated-result producer may queue this trigger."""
    with pytest.raises(ValueError):
        controller.notify("42", "receipt", kind=kind)
    assert not controller.path.exists()


def test_notify_and_ack_are_idempotent_across_controller_reload(module, controller):
    """Consumed worker receipts cannot resurrect through duplicate callbacks or reloads."""
    controller.observe("42", "origin")
    assert controller.notify("42", "x" * 200, now=1100)
    before = controller.path.read_bytes()
    assert not controller.notify("42", "x" * 200, now=1200)
    assert controller.path.read_bytes() == before
    reloaded = module.AutonomousController(controller.path)
    event, = reloaded.opportunities({}, now=1200)
    assert reloaded.ack(event, now=1200)
    consumed = controller.path.read_bytes()
    assert not controller.ack(event, now=1201)
    assert not controller.notify("42", "x" * 200, now=1201)
    assert controller.path.read_bytes() == consumed
    restarted = module.AutonomousController(controller.path)
    assert not restarted.notify("42", "x" * 200, now=1202)
    assert restarted.snapshot()["pending_events"] == {}
    assert restarted.opportunities({}, now=1202) == []


def test_result_ack_matches_exact_pending_receipt_and_preserves_schedules(controller):
    """An activity ack consumes one receipt, not a model check or a global tick."""
    controller.observe("42", "origin42")
    controller.observe("43", "origin43")
    controller.schedule("42", next_at=1100)
    controller.schedule(next_at=1600)
    controller.notify("42", "receipt-a", now=1100)
    controller.notify("43", "receipt-b", now=1100)
    before = controller.snapshot()
    events = controller.opportunities({}, now=1100)
    event = next(item for item in events if item["event_id"] == "activity:receipt-a")
    for change in ({"scope": "43"}, {"source_type": "tool_result"},
                   {"observed_at": 1099}, {"event_id": "activity:absent"}):
        assert not controller.ack(dict(event, **change), now=1101)
        assert controller.snapshot() == before
    assert controller.ack(event, now=1101)
    after = controller.snapshot()
    assert list(after["pending_events"]) == ["receipt-b"]
    for key in ("next_checks", "next_at", "last_run", "last_scope", "poll_last", "wake_slots"):
        assert after[key] == before[key]
    assert after["last_started"]["kind"] == "activity_result"
    assert {item["kind"] for item in controller.opportunities({}, now=1101)} == {
        "activity_result", "model_schedule"}


@pytest.mark.parametrize("restriction", ["muted", "mention_only"])
def test_restricted_results_remain_pending_until_allowed(controller, restriction):
    """Actual worker completions respect stored social policies without losing receipts."""
    controller.observe("42", "origin")
    controller.notify("42", "receipt", now=1100)
    assert controller.opportunities({restriction: ["42"]}, now=1100) == []
    assert "receipt" in controller.snapshot()["pending_events"]
    event, = controller.opportunities({}, now=1100)
    assert event["kind"] == "activity_result"


def test_disabled_results_remain_pending_without_overriding_explicit_poll(controller):
    """The global enable switch gates activity while keeping explicit poll semantics."""
    controller.observe("42", "origin")
    controller.schedule(enabled=False)
    assert controller.notify("42", "receipt", now=1100)
    events = controller.opportunities({"poll_minutes": {"42": 1}}, now=1100)
    assert [event["kind"] for event in events] == ["poll_interval"]
    assert "receipt" in controller.snapshot()["pending_events"]
    controller.schedule(enabled=True)
    event, = controller.opportunities({}, now=1100)
    assert event["kind"] == "activity_result"


@pytest.mark.parametrize("patch", [
    {"pending_events": []},
    {"notifications_seen": {}},
    {"notifications_seen": ["receipt", "receipt"]},
    {"notifications_seen": [7]},
    {"pending_events": {"receipt": []}},
    {"pending_events": {"receipt": {"scope": "42", "kind": "delegated_result",
                                    "observed_at": 1100}}},
    {"pending_events": {"receipt": {"scope": "043", "kind": "delegated_result",
                                    "observed_at": 1100}}, "origins": {"43": "origin"}},
    {"pending_events": {"receipt": {"scope": "42", "kind": "tool_result",
                                    "observed_at": 1100}}, "origins": {"42": "origin"}},
    {"pending_events": {"receipt": {"scope": "42", "kind": "delegated_result",
                                    "observed_at": None}}, "origins": {"42": "origin"}},
])
def test_invalid_persistent_activity_state_fails_closed(controller, patch):
    """Malformed completion provenance is rejected by shared loading, not executed."""
    controller.path.write_text(json.dumps(patch), encoding="utf-8")
    with pytest.raises((ValueError, TypeError)):
        controller.snapshot()


def test_pending_event_survives_load_without_seen_ledger(module, controller):
    """Pending-only saved data acquires durable deduplication when first acknowledged."""
    saved = {"origins": {"42": "origin"}, "last_run": 1000,
             "pending_events": {"receipt": {"scope": "42", "kind": "delegated_result",
                                             "observed_at": 1100}}}
    controller.path.write_text(json.dumps(saved), encoding="utf-8")
    event, = controller.opportunities({}, now=1100)
    assert controller.ack(event, now=1100)
    reloaded = module.AutonomousController(controller.path)
    assert not reloaded.notify("42", "receipt", now=1200)
