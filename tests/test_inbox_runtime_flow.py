"""Durable native ingress integration with production AST methods and offline HTTP."""

import asyncio
from pathlib import Path

from plugin.pending_inbox import InboxError, PendingInbox
from test_batch_flow import _entry, _envelope
from test_intake_split_flow import _http
from test_production_integration import Event

pytest_plugins = ("test_production_integration",)


class NativeEvent(Event):
    """Bind the existing Event to explicit native adapter/account evidence."""

    def __init__(self, text, message_id, self_id="90001"):
        super().__init__(text, message_id)
        self.self_id = self_id
        self.unified_msg_origin = "native-offline:GroupMessage:42"

    def get_platform_name(self):
        """Declare the genuine native ingress path, never WebChat response queues."""
        return "aiocqhttp"

    def get_platform_id(self):
        """Match the original platform identifier in unified_msg_origin exactly."""
        return "native-offline"

    def get_self_id(self):
        """Keep bot-account binding independent of sender and message IDs."""
        return self.self_id


def _root(plugin):
    """Use the shared temporary production root, never host/runtime credentials."""
    return Path(plugin._handle_one.__func__.__globals__["HIGAN_ROOT"])


async def _settle(plugin):
    """Await owned work only, leaving unrelated loop/framework activity untouched."""
    while plugin._background_tasks:
        await asyncio.wait_for(asyncio.gather(*tuple(plugin._background_tasks)), 5)


async def _crash_before_claim(plugin, events):
    """Simulate process loss while durable pending input waits behind the lock."""
    plugin._execution_lock = asyncio.Lock()
    await plugin._execution_lock.acquire()
    for event in events:
        await type(plugin).on_message(plugin, event)
    assert len(plugin._durable_inbox.pending()) == len(events)
    workers = tuple(plugin._background_tasks)
    for worker in workers:
        worker.cancel()
    await asyncio.gather(*workers, return_exceptions=True)
    plugin._execution_lock.release()


def test_pending_restart_replays_oldest_once_with_new_native_arrival(production, monkeypatch):
    """Two unstarted inputs survive process loss and precede the live triggering input."""
    old = production()
    _entry(old)
    first, second, current = (NativeEvent("input " + str(index), str(9100 + index)) for index in range(3))
    requests = _http(monkeypatch, "SILENT")

    async def scenario():
        """Cancel only preclaim work, then create a new process-shaped plugin instance."""
        await _crash_before_claim(old, [first, second])
        assert requests == []
        new = production()
        _entry(new)
        new._durable_inbox = PendingInbox(_root(new) / "pending_inbox.sqlite3", recover_started=True)
        await type(new).on_message(new, current)
        await _settle(new)
        assert new._durable_inbox.pending() == []
        # Same native identity is tombstoned: a repeated platform delivery is not a turn.
        before = len(requests)
        await type(new).on_message(new, NativeEvent(current.text, current.message_obj.message_id))
        await _settle(new)
        assert len(requests) == before
        return new

    new = asyncio.run(scenario())
    records = [record for body in requests for record in _envelope(body)["messages"]]
    assert [record["id"] for record in records] == ["9100", "9101", "9102"]
    assert [record["body"] for record in records] == [first.text, second.text, current.text]
    assert all(record["sender"]["id"] == "10001" and record["sender"]["role"] == "member" for record in records)
    assert not first.actions and not second.actions and not current.actions
    assert not any(new._pending.values()) and not new._busy["42"]


def test_started_crash_becomes_uncertain_and_cannot_trigger_model_again(production, monkeypatch):
    """An abandoned claimed batch is evidence of uncertainty, never automatic resend."""
    old = production()
    _entry(old)
    event = NativeEvent("already started", "9200")
    queue = PendingInbox(_root(old) / "pending_inbox.sqlite3")
    receipt = queue.enqueue("42", event, event.text, False, False)
    assert queue.claim([receipt["id"]])
    requests = _http(monkeypatch, "SILENT")
    new = production()
    _entry(new)
    new._durable_inbox = PendingInbox(_root(new) / "pending_inbox.sqlite3", recover_started=True)
    assert new._durable_inbox.pending() == []
    duplicate = new._durable_inbox.enqueue("42", event, event.text, False, False)
    assert duplicate["duplicate"] and duplicate["status"] == "uncertain"

    async def scenario():
        """Deliver the same identity again through actual production admission."""
        await type(new).on_message(new, event)
        await _settle(new)

    asyncio.run(scenario())
    assert requests == [] and event.sent == [] and event.actions == []


def test_other_account_does_not_replay_pending_and_current_account_still_runs(production, monkeypatch):
    """Another authenticated bot cannot claim old-account evidence or block new input."""
    plugin = production()
    _entry(plugin)
    old = NativeEvent("belongs to account A", "9300")
    plugin._durable_inbox = PendingInbox(_root(plugin) / "pending_inbox.sqlite3", recover_started=True)
    plugin._durable_inbox.enqueue("42", old, old.text, False, False)
    current = NativeEvent("account B input", "9301", self_id="90002")
    requests = _http(monkeypatch, "SILENT")

    async def scenario():
        """Exercise production recovery before admitting the actual matching-account input."""
        await type(plugin).on_message(plugin, current)
        await _settle(plugin)

    asyncio.run(scenario())
    assert [record["id"] for body in requests for record in _envelope(body)["messages"]] == ["9301"]
    assert [row["message_record"]["id"] for row in plugin._durable_inbox.pending()] == ["9300"]
    assert not old.actions and not current.actions


def test_policy_rejected_pending_is_blocked_without_model_or_tools(production, monkeypatch):
    """Current policy is rechecked before durable pending rows become running."""
    plugin = production()
    _entry(plugin)
    old = NativeEvent("now rejected", "9400")
    plugin._durable_inbox = PendingInbox(_root(plugin) / "pending_inbox.sqlite3")
    receipt = plugin._durable_inbox.enqueue("42", old, old.text, False, False)
    requests = _http(monkeypatch, "SILENT")
    monkeypatch.setattr(plugin, "_policy_allows", lambda event, scope: False)

    async def scenario():
        """Recover source explicitly, then let the actual worker reject current policy."""
        from plugin.inbox_runtime import recover
        assert recover(plugin, _root(plugin), old) == ["42"]
        plugin._busy["42"] = True
        await plugin._drain_pending("42")

    asyncio.run(scenario())
    assert plugin._durable_inbox.pending() == [] and requests == []
    assert plugin._inbox_event_ids == {}
    duplicate = plugin._durable_inbox.enqueue("42", old, old.text, False, False)
    assert duplicate == {"id": receipt["id"], "duplicate": True, "status": "blocked"}
    assert not old.actions and old.sent == []


def test_durable_admission_failure_never_starts_model_or_tools(production, monkeypatch, caplog):
    """Failing durable storage cannot fall through to an unclaimed native turn."""
    plugin = production()
    _entry(plugin)
    plugin._durable_inbox = PendingInbox(_root(plugin) / "pending_inbox.sqlite3")
    event = NativeEvent("must not execute", "9500")
    requests = _http(monkeypatch, "SILENT")

    def failed_write(*args, **kwargs):
        """Model a checked durable boundary failure without corrupting test files."""
        raise InboxError("offline_storage_failure")

    monkeypatch.setattr(plugin._durable_inbox, "enqueue", failed_write)

    async def scenario():
        """Actual on_message must stop before queueing any execution-capable input."""
        await type(plugin).on_message(plugin, event)
        await _settle(plugin)

    asyncio.run(scenario())
    assert requests == [] and event.sent == [] and event.actions == []
    assert not any(plugin._pending.values())
    assert caplog.records


def test_claim_exception_releases_local_identity_then_next_input_recovers_fifo(production, monkeypatch):
    """A pretransaction claim failure remains pending and is retried only on real input."""
    plugin = production()
    _entry(plugin)
    queue = PendingInbox(_root(plugin) / "pending_inbox.sqlite3")
    plugin._durable_inbox = queue
    old, new = NativeEvent("old pending input", "9600"), NativeEvent("new authentic input", "9601")
    requests = _http(monkeypatch, "SILENT")
    native_claim = queue.claim
    attempts = []

    def fail_once(identifiers):
        """Throw before touching SQLite once, then retain actual all-or-nothing claim."""
        attempts.append(list(identifiers))
        if len(attempts) == 1:
            raise InboxError("offline_pretransaction_claim_failure")
        return native_claim(identifiers)

    monkeypatch.setattr(queue, "claim", fail_once)

    async def scenario():
        """Assert no eager retry, then recover oldest pending before the new event."""
        await type(plugin).on_message(plugin, old)
        await _settle(plugin)
        assert requests == [] and len(attempts) == 1
        assert [row["message_record"]["id"] for row in queue.pending()] == ["9600"]
        assert plugin._inbox_event_ids == {} and not any(plugin._pending.values())
        await type(plugin).on_message(plugin, new)
        await _settle(plugin)

    asyncio.run(scenario())
    assert len(attempts) == 2 and len(attempts[1]) == 2
    assert [record["id"] for body in requests for record in _envelope(body)["messages"]] == ["9600", "9601"]
    assert queue.pending() == [] and plugin._inbox_event_ids == {}
    assert not old.actions and not new.actions and old.sent == [] and new.sent == []


def test_false_claim_terminal_race_releases_identity_and_unrelated_transient_executes(production, monkeypatch):
    """A lost terminal race cannot leave stale IDs that poison unsupported live input."""
    plugin = production()
    _entry(plugin)
    queue = PendingInbox(_root(plugin) / "pending_inbox.sqlite3")
    plugin._durable_inbox = queue
    old, transient = NativeEvent("lost race", "9700"), Event("unrelated transient input", "9701")
    requests = _http(monkeypatch, "SILENT")
    attempts = []

    def lose_race(identifiers):
        """Persist the competing terminal transition, then report no execution claim."""
        attempts.append(list(identifiers))
        assert queue.discard(identifiers, reason="policy_rejected")
        return False

    monkeypatch.setattr(queue, "claim", lose_race)

    async def scenario():
        """Follow a rejected durable turn with an unsupported but legitimate live event."""
        await type(plugin).on_message(plugin, old)
        await _settle(plugin)
        assert requests == [] and len(attempts) == 1
        assert plugin._inbox_event_ids == {} and queue.pending() == []
        await type(plugin).on_message(plugin, transient)
        await _settle(plugin)

    asyncio.run(scenario())
    assert len(attempts) == 1
    assert [record["id"] for body in requests for record in _envelope(body)["messages"]] == ["9701"]
    assert plugin._inbox_event_ids == {} and not any(plugin._pending.values())
    assert not old.actions and not transient.actions


def test_oversized_durable_input_discards_and_releases_local_identity(production, monkeypatch, caplog):
    """Archive-only input must lose its event mapping as well as its pending status."""
    plugin = production()
    _entry(plugin)
    queue = PendingInbox(_root(plugin) / "pending_inbox.sqlite3")
    plugin._durable_inbox = queue
    event = NativeEvent("H" * 100000, "9800")
    requests = _http(monkeypatch, "SILENT")

    async def scenario():
        """Admit and drain the real oversized native event without a model call."""
        await type(plugin).on_message(plugin, event)
        await _settle(plugin)

    asyncio.run(scenario())
    assert queue.pending() == [] and plugin._inbox_event_ids == {} and requests == []
    duplicate = queue.enqueue("42", event, event.text, False, False)
    assert duplicate["duplicate"] and duplicate["status"] == "blocked"
    assert "intake_archive_only" in caplog.text
    assert not event.actions and event.sent == []
