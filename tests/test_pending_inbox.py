"""Focused offline durability and restoration tests; no vendor boot or real sends."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import multiprocessing
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from plugin.pending_inbox import InboxError, PendingInbox, restore_event, snapshot_event
import plugin.pending_inbox as inbox_module


class NativeEvent:
    """Supply real adapter getter contracts and conflicting optional raw metadata."""

    def __init__(self, ident="10", scope="42", sender="10001", account="20002", segments=None):
        self.scope = scope
        self.sender = sender
        self.account = account
        self.unified_msg_origin = "napcat:" + ("FriendMessage:" + scope[8:] if scope.startswith("private:") else "GroupMessage:" + scope)
        self.message_obj = SimpleNamespace(message_id=ident, sender=SimpleNamespace(user_id="99999", nickname="raw name"),
                                           message=segments or [{"type": "text", "data": {"text": "hello"}}],
                                           raw_message={"password": "not-persisted", "self_id": "99999"})

    def get_sender_id(self):
        """The authenticated getter overrides stale raw identity metadata."""
        return self.sender

    def get_sender_name(self):
        """A role-looking display name remains untrusted display text."""
        return "owner/admin"

    def get_self_id(self):
        """Expose the actual receiving native QQ account."""
        return self.account

    def get_group_id(self):
        """Private native messages have no group destination."""
        return "" if self.scope.startswith("private:") else self.scope

    def get_platform_name(self):
        """Only the native OneBot adapter is eligible for durable restoration."""
        return "aiocqhttp"

    def get_platform_id(self):
        """The configured platform ID must match the real origin prefix."""
        return "napcat"

    def get_message_str(self):
        """Keep caller preprocessing separate from canonical source components."""
        return "hello"

    def get_messages(self):
        """Return only the current top-level canonical segment stream."""
        return self.message_obj.message


def admit(store, ident="10", **kwargs):
    """Admit one deterministic native test message without exposing body in output."""
    event = NativeEvent(ident, **kwargs)
    return store.enqueue(event.scope, event, "hello", False, True, history=[{"role": "system", "content": "never-store"}])


def test_transaction_claim_is_atomic_and_startup_quarantines_started(tmp_path):
    """Only still-pending work survives restart; one missing ID aborts full claim."""
    path = tmp_path / "pending.sqlite3"
    store = PendingInbox(path)
    first, second = admit(store), admit(store, "11")
    ids = [first["id"], second["id"]]
    assert store.claim(ids + [99999]) is False
    assert [row["id"] for row in store.pending()] == ids
    assert store.claim([ids[0]]) is True
    assert store.claim(ids) is False
    # Opening a normal second handle must not demote another live worker's claim.
    assert PendingInbox(path).complete([ids[0]]) is True
    assert store.claim([ids[1]]) is True
    restored = PendingInbox(path, recover_started=True)
    assert restored.pending() == []
    assert admit(restored)["status"] == "completed"
    uncertain = admit(restored, "11")
    assert uncertain == {"id": ids[1], "status": "uncertain", "duplicate": True}
    assert restored.claim([ids[1]]) is False
    assert restored.complete([ids[1]]) is False


def test_unstarted_survives_restart_and_dedup_tombstones_are_per_account(tmp_path):
    """Exact routing/account/native ID deduplication never invents a replay attempt."""
    path = tmp_path / "pending.sqlite3"
    store = PendingInbox(path)
    first = admit(store)
    assert admit(store) == dict(first, duplicate=True)
    assert admit(store, account="20003")["id"] != first["id"]
    restored = PendingInbox(path, recover_started=True)
    rows = restored.pending()
    assert [row["id"] for row in rows] == [1, 2]
    assert "history" not in rows[0] and "role" not in rows[0]
    assert restored.claim([first["id"]])
    assert restored.complete([first["id"]], "failed")
    assert restored.complete([first["id"]], "failed")
    assert not restored.complete([first["id"]], "completed")
    assert admit(restored)["status"] == "failed"


def test_corrupt_database_fails_closed_without_empty_overwrite(tmp_path):
    """Existing corrupt bytes are not replaced with a misleading empty store."""
    path = tmp_path / "pending.sqlite3"
    original = b"not a sqlite database: retained evidence"
    path.write_bytes(original)
    with pytest.raises(InboxError):
        PendingInbox(path, recover_started=True)
    assert path.read_bytes() == original


def test_existing_empty_file_is_not_adopted_as_new_store(tmp_path):
    """A truncated database cannot be silently overwritten with empty valid state."""
    path = tmp_path / "pending.sqlite3"
    path.touch()
    with pytest.raises(InboxError):
        PendingInbox(path)
    assert path.read_bytes() == b""


def test_default_store_lives_at_configured_higan_root(tmp_path, monkeypatch):
    """Use the centralized runtime root without an additional environment parser."""
    root = tmp_path / "higan"
    monkeypatch.setenv("HIGAN_ROOT", str(root))
    store = PendingInbox()
    assert store.path == root / "pending_inbox.sqlite3"
    assert store.path.exists()
    assert admit(store)["status"] == "pending"


def test_restoration_binds_real_source_and_requires_connected_account(tmp_path):
    """Restored source gets true attribution, not task identity or an inferred role."""
    store = PendingInbox(tmp_path / "pending.sqlite3")
    admit(store)
    row = store.pending()[0]
    restored = restore_event(SimpleNamespace(), row, platform="napcat", self_id="20002")
    assert restored.restored_input is True
    assert restored.stored_sender == {"id": "10001", "display_name": "owner/admin"}
    assert restored.get_sender_id() == "10001" and restored.get_user_id() == "10001" and restored.get_self_id() == "20002"
    assert restored.get_group_id() == "42" and restored.message_obj.message_id == "10"
    assert not hasattr(restored, "role") and not hasattr(restored, "is_admin")
    assert "raw_message" not in vars(restored.message_obj)
    with pytest.raises(InboxError):
        restore_event(SimpleNamespace(), row, platform="other", self_id="20002")
    with pytest.raises(InboxError):
        restore_event(SimpleNamespace(), row, platform="napcat", self_id="20003")
    receipt = store.enqueue("42", restored, "hello", False, True)
    assert receipt["status"] == "unsupported"


@pytest.mark.parametrize("scope,sender,expected", [
    ("42", "10001", "42"), ("private:10001", "10001", ""),
])
def test_source_projection_exact_record_mentions_quotes_and_attachments(tmp_path, scope, sender, expected):
    """Native IDs/body/source associations survive, but URL secrets/raw payload do not."""
    segments = [{"type": "text", "data": {"text": "hello"}},
                {"type": "at", "data": {"qq": "20002", "name": "native bot"}},
                {"type": "reply", "data": {"id": "-99", "sender": {"user_id": "30003", "nickname": "quoted"}, "body": "quoted source"}},
                {"type": "image", "data": {"url": "https://cdn.example/image?token=private#secret", "file": "image.png"}},
                {"type": "file", "data": {"id": "native-file", "name": "report.txt", "size": 123}},
                {"type": "record", "data": {"url": "https://user:password@cdn.example/voice"}}]
    store = PendingInbox(tmp_path / "pending.sqlite3")
    event = NativeEvent("-10", scope, sender, segments=segments)
    receipt = store.enqueue(scope, event, "hello", True, True)
    assert receipt["status"] == "pending"
    row = store.pending()[0]
    record = row["message_record"]
    assert record["id"] == "-10" and record["sender"]["id"] == sender
    assert record["mentions"] == [{"id": "20002", "display_name": "native bot"}]
    assert record["quote"] == {"id": "-99", "sender": {"id": "30003", "display_name": "quoted"}, "body": "quoted source"}
    assert row["attachments"][0]["references"]["url"] == {
        "location": "https://cdn.example/image", "unavailable": "url_capability_not_persisted"}
    assert row["attachments"][2]["references"]["url"] == {"unavailable": "credential_url_not_persisted"}
    serialized = json.dumps(row)
    assert "private#secret" not in serialized and "password" not in serialized and "raw_message" not in serialized
    restored = restore_event(SimpleNamespace(), row, platform="napcat", self_id="20002")
    assert restored.get_group_id() == expected
    assert restored.stored_message_record == record
    assert restored.attachment_sources == row["attachments"]
    assert restored.get_messages()[-1] == {"type": "record", "data": {}}
    # Mutating a returned dict must never mutate the underlying durable snapshot.
    row["message_record"]["sender"]["id"] = "99999"
    assert store.pending()[0]["message_record"]["sender"]["id"] == sender
    assert restored.get_sender_id() == sender


@pytest.mark.parametrize("scope,origin,sender,account,group", [
    ("４２", "napcat:GroupMessage:４２", "10001", "20002", "４２"),
    ("42", "napcat:FriendMessage:42", "10001", "20002", "42"),
    ("42", "napcat:GroupMessage:43", "10001", "20002", "42"),
    ("private:10001", "napcat:FriendMessage:10001", "10002", "20002", ""),
    ("private:10001", "napcat:FriendMessage:10001", "10001", "20002", "42"),
    ("42", "napcat:GroupMessage:42", "background-task", "20002", "42"),
    ("42", "napcat:GroupMessage:42", "10001", "", "42"),
    ("0", "napcat:GroupMessage:0", "10001", "20002", "0"),
    ("42", "napcat:GroupMessage:42:extra", "10001", "20002", "42"),
])
def test_invalid_identity_or_exact_scope_origin_is_explicit_unsupported(tmp_path, scope, origin, sender, account, group):
    """Never trust free-form destinations, mismatched getters or Unicode numeric lookalikes."""
    store = PendingInbox(tmp_path / "pending.sqlite3")
    event = NativeEvent(sender=sender, account=account)
    event.unified_msg_origin = origin
    event.get_group_id = lambda: group
    receipt = store.enqueue(scope, event, "hello", False, True)
    assert receipt["id"] is None and receipt["status"] == "unsupported" and receipt["duplicate"] is False
    assert snapshot_event(scope, event, "hello", False, True) is None
    assert store.pending() == []


@pytest.mark.parametrize("platform", ["webchat", "local", "other", ""])
def test_transient_or_unknown_adapter_never_persists_response_capabilities(tmp_path, platform):
    """No WebChat queue, local session, synthetic task or raw event object is serialized."""
    store = PendingInbox(tmp_path / "pending.sqlite3")
    event = NativeEvent()
    event.get_platform_name = lambda: platform
    event.response_queue = object()
    receipt = store.enqueue("42", event, "hello", False, True)
    assert receipt == {"id": None, "status": "unsupported", "duplicate": False, "reason": "unsupported_platform"}
    assert store.pending() == []


def test_platform_getter_mismatch_and_failed_getter_are_not_replayable(tmp_path):
    """A real account observation cannot be borrowed from another origin/adapter."""
    store = PendingInbox(tmp_path / "pending.sqlite3")
    event = NativeEvent()
    event.get_platform_id = lambda: "other"
    assert store.enqueue("42", event, "hello", False, True)["reason"] == "platform_mismatch"
    event.get_platform_id = lambda: "napcat"

    def broken_getter():
        """Model a framework source getter that cannot prove sender identity."""
        raise RuntimeError("not logged")

    event.get_sender_id = broken_getter
    assert store.enqueue("42", event, "hello", False, True)["reason"] == "source_getter_failed"
    assert store.pending() == []


@pytest.mark.parametrize("status", ["completed", "failed", "blocked"])
def test_all_terminal_states_deduplicate_and_cannot_be_reclaimed(tmp_path, status):
    """Acknowledged native IDs remain tombstones regardless of eventual turn outcome."""
    store = PendingInbox(tmp_path / "pending.sqlite3")
    receipt = admit(store)
    assert store.complete([receipt["id"]], status) is False
    assert store.claim([receipt["id"]])
    assert store.complete([receipt["id"]], status)
    assert store.complete([receipt["id"]], status)
    assert admit(store) == {"id": receipt["id"], "status": status, "duplicate": True}
    assert store.claim([receipt["id"]]) is False
    assert store.pending() == []


def test_discard_blocks_only_pending_and_never_uncertain_or_running(tmp_path):
    """Policy/oversize refusal archives unstarted input without granting execution."""
    path = tmp_path / "pending.sqlite3"
    store = PendingInbox(path)
    first, second, third = [admit(store, str(ident))["id"] for ident in (10, 11, 12)]
    assert store.claim([second, third])
    assert store.discard([first, second]) is False
    assert store.discard([first], reason="input_oversized")
    assert store.discard([first])
    recovered = PendingInbox(path, recover_started=True)
    assert recovered.discard([third]) is False
    assert admit(recovered)["status"] == "blocked"
    with pytest.raises(ValueError):
        recovered.discard([first], reason="arbitrary role prose")


@pytest.mark.parametrize("ids", [[1, 1], [True], [0], [-1], ["1"], "1", {1}])
def test_batch_identifier_validation_never_partially_claims(tmp_path, ids):
    """Malformed/duplicated IDs cannot turn one side effect into multiple attempts."""
    store = PendingInbox(tmp_path / "pending.sqlite3")
    admit(store)
    with pytest.raises(ValueError):
        store.claim(ids)
    assert len(store.pending()) == 1


def test_sql_failure_rolls_back_full_claim(tmp_path):
    """Even a failure after the first update cannot leave a half-running batch."""
    path = tmp_path / "pending.sqlite3"
    store = PendingInbox(path)
    ids = [admit(store, str(ident))["id"] for ident in (10, 11)]
    with sqlite3.connect(path) as db:
        db.execute("CREATE TRIGGER fail_second BEFORE UPDATE OF status ON pending_inbox WHEN NEW.id=2 AND NEW.status='running' BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    with pytest.raises(InboxError):
        store.claim(ids)
    assert [row["id"] for row in store.pending()] == ids


def test_oversized_and_secret_url_text_reject_whole_input(tmp_path, monkeypatch):
    """No message is silently truncated/redacted and no signed URL survives in prose."""
    store = PendingInbox(tmp_path / "pending.sqlite3")
    monkeypatch.setattr(inbox_module, "MAX_PAYLOAD_BYTES", 1024)
    receipt = store.enqueue("42", NativeEvent(), "字" * 1000, False, True)
    assert receipt["reason"] == "payload_too_large"
    for text in ("https://example.org/a?token=secret", "https://user:pw@example.org/a", "https://example.org/a#secret"):
        receipt = store.enqueue("42", NativeEvent(), text, False, True)
        assert receipt["reason"] == "secret_url_not_persisted"
    assert store.pending() == []


def test_maximal_admitted_payload_still_restores_with_row_metadata(tmp_path, monkeypatch):
    """The id/status wrapper must not break restoration at the exact byte ceiling."""
    event = NativeEvent()
    payload = snapshot_event("42", event, "hello", False, True)
    bound = len(inbox_module._encode(payload).encode("utf-8"))
    monkeypatch.setattr(inbox_module, "MAX_PAYLOAD_BYTES", bound)
    store = PendingInbox(tmp_path / "pending.sqlite3")
    assert store.enqueue("42", event, "hello", False, True)["status"] == "pending"
    restored = restore_event(SimpleNamespace(), store.pending()[0], platform="napcat", self_id="20002")
    assert restored.get_message_str() == "hello"


def test_foreign_or_payload_corrupt_db_is_not_replaced(tmp_path):
    """Both valid foreign SQLite and corrupt row digests close the recovery boundary."""
    foreign = tmp_path / "foreign.sqlite3"
    with sqlite3.connect(foreign) as db:
        db.execute("CREATE TABLE unrelated(data TEXT)")
        db.execute("INSERT INTO unrelated VALUES ('preserved')")
    with pytest.raises(InboxError):
        PendingInbox(foreign)
    with sqlite3.connect(foreign) as db:
        assert db.execute("SELECT data FROM unrelated").fetchone() == ("preserved",)
    path = tmp_path / "pending.sqlite3"
    store = PendingInbox(path)
    admit(store)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE pending_inbox SET payload='{}'")
    with pytest.raises(InboxError):
        store.pending()
    with pytest.raises(InboxError):
        PendingInbox(path, recover_started=True)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT payload FROM pending_inbox").fetchone() == ("{}",)


@pytest.mark.parametrize("mutation", ["role", "origin", "self", "sender", "status", "attachment"])
def test_restore_revalidates_metadata_and_rejects_injected_authority(tmp_path, mutation):
    """Even structurally valid data must retain exact route/account/current source."""
    store = PendingInbox(tmp_path / "pending.sqlite3")
    admit(store)
    row = store.pending()[0]
    if mutation == "role":
        row["role"] = "owner"
    elif mutation == "origin":
        row["origin"] = "napcat:GroupMessage:99"
    elif mutation == "self":
        row["self_id"] = "99999"
    elif mutation == "sender":
        row["message_record"]["sender"]["role"] = "admin"
    elif mutation == "status":
        row["status"] = "uncertain"
    else:
        row["attachments"] = [{"type": "image", "source": {"sender": {"id": "9"}}, "metadata": {}, "references": {}}]
    with pytest.raises(InboxError):
        restore_event(SimpleNamespace(), row, platform="napcat", self_id="20002")


def test_stored_route_mirror_tampering_is_detected_even_with_valid_digest(tmp_path):
    """The payload's scope cannot diverge from durable deduplication/routing columns."""
    path = tmp_path / "pending.sqlite3"
    store = PendingInbox(path)
    admit(store)
    with sqlite3.connect(path) as db:
        raw = db.execute("SELECT payload FROM pending_inbox").fetchone()[0]
        payload = json.loads(raw)
        payload["scope"] = "99"
        payload["origin"] = "napcat:GroupMessage:99"
        raw = json.dumps(payload)
        db.execute("UPDATE pending_inbox SET payload=?,digest=?", (raw, hashlib.sha256(raw.encode()).hexdigest()))
    with pytest.raises(InboxError):
        store.pending()


def _process_enqueue(path):
    """Exercise SQLite cross-process locking without external transports/vendor code."""
    store = PendingInbox(path)
    for ident in range(100, 108):
        admit(store, str(ident))


def test_threads_and_processes_share_atomic_duplicate_admission(tmp_path):
    """Many handles/processes cannot create two rows for one exact native source."""
    path = tmp_path / "pending.sqlite3"
    store = PendingInbox(path)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: admit(PendingInbox(path)), range(12)))
    assert len({receipt["id"] for receipt in results}) == 1
    assert sum(not receipt["duplicate"] for receipt in results) == 1
    spawn = multiprocessing.get_context("spawn")
    workers = [spawn.Process(target=_process_enqueue, args=(str(path),)) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=15)
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=5)
            pytest.fail("offline inbox process timed out")
        assert worker.exitcode == 0
    assert len(store.pending()) == 9


def test_restored_send_reuses_native_serializer_once_without_retry(tmp_path, monkeypatch):
    """Restoration reuses TaskEvent's receipt-confirmed native transport only."""
    store = PendingInbox(tmp_path / "pending.sqlite3")
    admit(store)
    calls = []

    async def parse(chain):
        """Stand in only for the already-supported native chain serializer."""
        return [{"type": "text", "data": {"text": "reply"}}]

    def native(action, parameters):
        """Record one offline native send while returning an actual-style receipt."""
        calls.append((action, parameters))
        return {"status": "ok", "retcode": 0, "data": {"message_id": 765}}

    module = "astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event"
    monkeypatch.setitem(sys.modules, module, SimpleNamespace(AiocqhttpMessageEvent=SimpleNamespace(_parse_onebot_json=parse)))
    event = restore_event(SimpleNamespace(_onebot_http=native), store.pending()[0], platform="napcat", self_id="20002")
    assert store.claim([event.inbox_id])
    sent = asyncio.run(event.send(SimpleNamespace()))
    assert sent[0]["message_id"] == "765" and len(calls) == 1
    assert calls[0][0] == "send_group_msg" and calls[0][1]["group_id"] == 42
    assert store.complete([event.inbox_id])
    assert admit(store)["duplicate"] is True
