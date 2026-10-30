"""Wake-person storage regressions use only synthetic identities and local disk."""

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugin.memory import WakePersonFacts
from plugin import wake_persons
from plugin.wake_persons import dispatch_person_facts, observe_wake_person_message
from types import SimpleNamespace


def _event(text="actual native utterance", *, qq="10001", mid="m1", segments=None):
    """Expose synthetic native getters without importing the framework or credentials."""
    return SimpleNamespace(get_sender_id=lambda: qq, get_sender_name=lambda: "fixture person",
        get_message_str=lambda: text, get_messages=lambda: segments or [],
        message_obj=SimpleNamespace(message_id=mid, message=segments or []))


def _configure(tmp_path, persons=None):
    """Persist only the fixture membership used by real automatic intake."""
    config = {"wake_persons": persons or {"42": ["10001"], "77": ["10001"],
                                          "private:10001": ["10001"]}}
    (tmp_path / "groups.json").write_text(json.dumps(config), encoding="utf-8")
    return config


def _store(tmp_path):
    """Read from disk each time, never passing by an instance's in-memory cache."""
    return WakePersonFacts(str(tmp_path / "memory" / "wake_persons.json")).load()


def test_corruption_never_replaced_by_an_empty_store(tmp_path):
    """Malformed durable state must not turn a later add into silent data loss."""
    path = tmp_path / "wake_persons.json"
    path.write_text("{broken-json", encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises((ValueError, OSError)):
        WakePersonFacts(str(path)).load().add("10001", "must not replace corrupt bytes")
    assert path.read_bytes() == before


def test_explicit_add_currently_persists_on_reload(tmp_path):
    """Pin the existing successful path so repair targets missing automatic ingestion."""
    path = tmp_path / "wake_persons.json"
    fid = WakePersonFacts(str(path)).load().add("10001", "explicit model-written fact")
    assert fid and WakePersonFacts(str(path)).load().list("10001")[0]["text"] == "explicit model-written fact"
    assert json.loads(path.read_text(encoding="utf-8"))["persons"]["10001"]


def test_native_intake_reload_and_source_id_idempotence(tmp_path):
    """Message IDs deduplicate real retransmits, not different messages with equal prose."""
    _configure(tmp_path)
    first = observe_wake_person_message(str(tmp_path), "42", _event(), "expanded unrelated text")
    path = tmp_path / "memory" / "wake_persons.json"
    before = path.read_bytes()
    duplicate = observe_wake_person_message(str(tmp_path), "group:42", _event(text="changed retry"), "retry")
    assert duplicate == first and path.read_bytes() == before
    observe_wake_person_message(str(tmp_path), "42", _event(mid="m2"), "any")
    rows = _store(tmp_path).list("10001", current_scope="42")
    assert len(rows) == 2 and rows[0]["kind"] == "observed_message"
    assert rows[0]["text"] == "actual native utterance"
    assert rows[0]["source"]["scope"] == "42"
    assert rows[0]["source"]["message_ids"] == ["m1"]
    assert rows[0]["source"]["messages"][0]["sender"]["id"] == "10001"
    assert _store(tmp_path).data["version"] == 2


def test_membership_scope_and_private_boundaries_fail_closed(tmp_path):
    """Other speakers/groups, guessed suffix scopes and private chats never auto-enroll."""
    _configure(tmp_path, {"42": ["10001"]})
    for scope, event in [("77", _event()), ("private:10001", _event()),
                         ("malicious42", _event()), ("42", _event(qq="10002")),
                         ("42", _event(mid=""))]:
        assert observe_wake_person_message(str(tmp_path), scope, event, "unused") == ""
    assert not (tmp_path / "memory" / "wake_persons.json").exists()
    observe_wake_person_message(str(tmp_path), "42", _event(), "unused")
    assert _store(tmp_path).list("10001", current_scope="77") == []
    assert _store(tmp_path).search("10001", "", current_scope="private:10001") == []
    assert _store(tmp_path).delete("10001", keyword="utterance", current_scope="77") == 0
    assert _store(tmp_path).count("10001", current_scope="42") == 1


def test_same_message_id_in_another_scope_is_not_duplicate(tmp_path):
    """QQ+source scope+native ID form the observation identity, including private scopes."""
    _configure(tmp_path)
    for scope in ("42", "77", "private:10001"):
        assert observe_wake_person_message(str(tmp_path), scope, _event(), "unused")
    for scope in ("42", "77", "private:10001"):
        assert _store(tmp_path).count("10001", current_scope=scope) == 1
    assert _store(tmp_path).count("10001") == 3


def test_expanded_forwards_and_quotes_are_not_original_speech(tmp_path):
    """Only canonical top-level Plain text is attributed to a configured sender."""
    _configure(tmp_path)
    components = [{"type": "reply", "data": {"id": "q1", "text": "quoted private assertion"}},
                  {"type": "forward", "data": {"id": "f1"}},
                  {"type": "text", "data": {"text": "actual sentence"}}]
    event = _event("rendered quoted material", segments=components)
    observe_wake_person_message(str(tmp_path), "42", event, "actual sentence\nexpanded forwarded secret")
    row = _store(tmp_path).list("10001")[0]
    assert row["text"] == "actual sentence"
    assert "quoted" not in json.dumps(row) and "forwarded" not in json.dumps(row)
    event = _event(mid="m2", segments=components[:2])
    assert observe_wake_person_message(str(tmp_path), "42", event, "expanded forward") == ""


def test_model_facts_separate_from_observations_and_permission_gated(tmp_path):
    """Tools cannot auto-enroll QQs, fabricate source metadata, or relabel observations."""
    _configure(tmp_path)
    event = _event()
    observe_wake_person_message(str(tmp_path), "42", event, "unused")
    result = dispatch_person_facts("wp_fact_add", {"qq": "10001", "text": "actual native utterance",
        "source": {"scope": "77", "message_ids": ["invented"]}}, root=str(tmp_path),
        current_scope="42", event=event, can_write=True)
    assert result["ok"] and result["persisted"] and result["kind"] == "model_fact"
    rows = _store(tmp_path).list("10001", current_scope="42")
    assert [row["kind"] for row in rows] == ["observed_message", "model_fact"]
    assert rows[1]["source"]["message_ids"] == ["m1"]
    path = tmp_path / "memory" / "wake_persons.json"
    before = path.read_bytes()
    for name in ("wp_fact_add", "wp_fact_delete", "wp_fact_move"):
        assert dispatch_person_facts(name, {"qq": "10001", "text": "unauthorized"},
            root=str(tmp_path), current_scope="42")["ok"] is False
    assert dispatch_person_facts("wp_fact_add", {"qq": "10002", "text": "not a member"},
        root=str(tmp_path), current_scope="42", event=event, can_write=True)["ok"] is False
    assert path.read_bytes() == before


def test_v1_records_survive_but_unknown_origin_not_disclosed(tmp_path):
    """Legacy provenance is never invented; new scoped writes preserve the old rows."""
    _configure(tmp_path)
    path = tmp_path / "memory" / "wake_persons.json"
    path.parent.mkdir()
    legacy = {"id": "w001", "text": "unknown legacy source", "at": 1}
    path.write_text(json.dumps({"version": 1, "persons": {"10001": [legacy]}}), encoding="utf-8")
    assert _store(tmp_path).list("10001", current_scope="42") == []
    observe_wake_person_message(str(tmp_path), "42", _event(), "unused")
    assert _store(tmp_path).data["persons"]["10001"][0] == legacy
    assert _store(tmp_path).count("10001", current_scope="42") == 1


def test_atomic_replace_failure_preserves_bytes_and_cleans_temp(tmp_path, monkeypatch):
    """A disk failure raises without false success, losing the old file, or temp debris."""
    path = tmp_path / "wake_persons.json"
    store = WakePersonFacts(str(path)).load()
    store.add("10001", "first")
    before = path.read_bytes()

    def deny_replace(source, destination):
        """Inject only replacement failure; no production disk or permissions change."""
        raise OSError("fixture replacement failure")

    monkeypatch.setattr(wake_persons.os, "replace", deny_replace)
    with pytest.raises(OSError):
        store.add("10001", "second")
    assert path.read_bytes() == before
    assert [row["text"] for row in store.list("10001")] == ["first"]
    assert list(tmp_path.glob(".wake-person-*.tmp")) == []


def test_stale_instances_reload_and_more_than_200_are_retained(tmp_path):
    """Short-lived instances merge from disk and archival rows are not count-pruned."""
    path = tmp_path / "wake_persons.json"
    first, stale = WakePersonFacts(str(path)).load(), WakePersonFacts(str(path)).load()
    first.add("10001", "first")
    stale.add("10001", "second")
    for index in range(200):
        first.add("10001", "record-%s" % index)
    rows = WakePersonFacts(str(path)).load().list("10001")
    assert len(rows) == 202 and rows[0]["text"] == "first"
    assert len({row["id"] for row in rows}) == 202


@pytest.mark.parametrize("payload", [[], {"version": 2, "persons": []},
    {"version": 2, "persons": {"10001": ["not a record"]}},
    {"version": 2, "persons": {"10001": [{"id": "w1", "text": "x", "source": []}]}}])
def test_malformed_schema_fails_without_replacing_bytes(tmp_path, payload):
    """Valid JSON with invalid structure is corruption, not permission to reset."""
    path = tmp_path / "wake_persons.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError):
        WakePersonFacts(str(path)).load().add("10001", "must not erase invalid data")
    assert path.read_bytes() == before


def test_delete_scope_and_repeated_mutations_are_noops(tmp_path):
    """Same text in different groups/private stays isolated through edits and deletions."""
    _configure(tmp_path)
    for scope in ("42", "77", "private:10001"):
        observe_wake_person_message(str(tmp_path), scope, _event(), "unused")
    rows = _store(tmp_path).list("10001", current_scope="42")
    assert _store(tmp_path).delete("10001", rows[0]["id"], current_scope="42") == 1
    before = (tmp_path / "memory" / "wake_persons.json").read_bytes()
    assert _store(tmp_path).delete("10001", rows[0]["id"], current_scope="42") == 0
    assert (tmp_path / "memory" / "wake_persons.json").read_bytes() == before
    assert _store(tmp_path).count("10001", current_scope="42") == 0
    assert _store(tmp_path).count("10001", current_scope="77") == 1
    assert _store(tmp_path).count("10001", current_scope="private:10001") == 1


def test_move_preserves_provenance_and_only_removes_after_destination_save(tmp_path, monkeypatch):
    """A failed checked destination write never deletes the source fact's durable copy."""
    from plugin.memory import Memory

    _configure(tmp_path)
    source_path = tmp_path / "memory" / "memory.json"
    source = Memory(str(source_path)).load()
    source_evidence = WakePersonFacts.source("42", wake_persons.native_record(_event()))
    fid = source.add_fact("fact to move", "42", source=source_evidence)
    before = source_path.read_bytes()
    replace = wake_persons.os.replace

    def fail_person_replace(source, destination):
        """Fail only the destination file, retaining the source store's normal path."""
        if str(destination).endswith("wake_persons.json"):
            raise OSError("fixture destination failure")
        return replace(source, destination)

    monkeypatch.setattr(wake_persons.os, "replace", fail_person_replace)
    with pytest.raises(OSError):
        dispatch_person_facts("wp_fact_move", {"qq": "10001", "id": fid}, root=str(tmp_path),
                              current_scope="42", event=_event(), can_write=True)
    assert source_path.read_bytes() == before
    monkeypatch.setattr(wake_persons.os, "replace", replace)
    moved = dispatch_person_facts("wp_fact_move", {"qq": "10001", "id": fid}, root=str(tmp_path),
                                 current_scope="42", event=_event(), can_write=True)
    assert moved["ok"] and moved["persisted"]
    assert Memory(str(source_path)).load().list_facts(None, current_scope="42") == []
    row = _store(tmp_path).list("10001", current_scope="42")[0]
    assert row["source"] == source_evidence and row["kind"] == "model_fact"


def test_person_info_and_fact_reads_do_not_expose_other_memberships(tmp_path):
    """Membership, timers and observations from other scopes remain hidden in tools."""
    config = _configure(tmp_path)
    config["wake_at"] = {"42": ["local time"], "77": ["other group time"]}
    (tmp_path / "groups.json").write_text(json.dumps(config), encoding="utf-8")
    observe_wake_person_message(str(tmp_path), "42", _event(text="local body"), "unused")
    observe_wake_person_message(str(tmp_path), "77", _event(text="other group body"), "unused")
    info = dispatch_person_facts("wake_person_info", {"qq": "10001"}, root=str(tmp_path),
                                 current_scope="42", event=_event())
    assert info["is_wake_person_in"] == ["42"] and info["wake_at"] == {"42": ["local time"]}
    assert info["facts_count"] == 1 and info["facts"][0]["text"] == "local body"
    listed = dispatch_person_facts("wp_fact_list", {"qq": "10001"}, root=str(tmp_path),
                                  current_scope="42", event=_event())
    assert "other group body" not in json.dumps(listed)


def test_parallel_short_lived_stores_do_not_lose_messages(tmp_path):
    """One shared lock covers independent tool/intake instances in the bot process."""
    from concurrent.futures import ThreadPoolExecutor

    _configure(tmp_path)

    def ingest(index):
        """Create a fresh store through the production intake helper for each message."""
        return observe_wake_person_message(str(tmp_path), "42", _event(mid="parallel-%s" % index), "unused")

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(ingest, range(16)))
    assert len(set(ids)) == 16 and _store(tmp_path).count("10001", current_scope="42") == 16
