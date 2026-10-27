"""Exercise standalone durable claims without network, credentials or app DBs."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import sqlite3

import pytest

from qq_transport.store import (
    OperationJournal, canonical_json, copy_json,
    validate_account, validate_identifier,
)


@pytest.fixture
def journal(tmp_path):
    """Provide an explicitly selected private temporary SQLite journal."""
    return OperationJournal(tmp_path / "nested" / "operations.sqlite3")


def test_claim_pending_persists_across_instances(journal):
    """Unknown outcomes stay pending forever, including after reopening."""
    expected = {"claimed": True, "conflict": False,
                "state": "pending", "response": None}
    assert journal.claim("123", "opaque:Op", "send_msg", {"x": "hello"}) == expected
    reopened = OperationJournal(journal.path)
    expected["claimed"] = False
    assert reopened.claim("123", "opaque:Op", "send_msg", {"x": "hello"}) == expected


def test_complete_replay_is_fresh_and_immutable(journal):
    """First canonical completion is authoritative and replay never aliases it."""
    journal.claim("123", "op", "send_msg", {})
    response = {"status": "ok", "data": {"message_id": 12, "text": "中文"}}
    assert journal.complete("123", "op", response) is None
    response["data"]["message_id"] = 99
    replay = journal.claim("123", "op", "send_msg", {})
    assert replay["state"] == "complete"
    assert replay["response"]["data"]["message_id"] == 12
    replay["response"]["data"]["message_id"] = 20
    stable = {"data": {"text": "中文", "message_id": 12}, "status": "ok"}
    journal.complete("123", "op", stable)
    with pytest.raises(ValueError, match="immutable"):
        journal.complete("123", "op", {"status": "other"})
    assert OperationJournal(journal.path).claim("123", "op", "send_msg", {})["response"] == stable


@pytest.mark.parametrize("completed", [False, True])
def test_conflicts_never_leak_original_response(journal, completed):
    """Different actions or parameter bodies cannot reuse a claimed key."""
    journal.claim("123", "op", "send_msg", {"a": 1})
    if completed:
        journal.complete("123", "op", {"private": "response"})
    for action, params in [("get_msg", {"a": 1}), ("send_msg", {"a": 2})]:
        result = journal.claim("123", "op", action, params)
        assert result == {"claimed": False, "conflict": True,
                          "state": "complete" if completed else "pending",
                          "response": None}


def test_account_and_identifier_spelling_is_preserved(journal):
    """Separate accounts and case-sensitive opaque IDs have separate claims."""
    for account, operation in [("123", "op"), ("124", "op"),
                               ("0123", "op"), ("123", "OP")]:
        assert journal.claim(account, operation, "test_auto_register_01", {})["claimed"]
    assert validate_account("0123") == "0123"
    assert validate_identifier(" opaque:中文 ") == " opaque:中文 "


def test_canonical_equivalence_and_storage_minimization(journal):
    """Object order is irrelevant and raw request parameters are not persisted."""
    params = {"z": [1, "secret request text"], "a": {"two": 2, "one": 1}}
    journal.claim("123", "op", "send_msg", params)
    reordered = {"a": {"one": 1, "two": 2}, "z": [1, "secret request text"]}
    assert not journal.claim("123", "op", "send_msg", reordered)["conflict"]
    with sqlite3.connect(journal.path) as connection:
        row = connection.execute("SELECT * FROM operations").fetchone()
        columns = [column[1] for column in connection.execute("PRAGMA table_info(operations)")]
    assert columns == ["account", "operation_id", "action", "fingerprint", "state",
                       "response", "created_at", "updated_at"]
    assert row[3] == hashlib.sha256(canonical_json(params).encode("utf-8")).hexdigest()
    assert "secret request text" not in repr(row)
    assert row[6] == row[7]


def test_ten_concurrent_claims_have_one_winner(journal):
    """SQLite serializes independent connections without duplicate permission."""
    def attempt(_index):
        """Compete through an independent journal and SQLite connection."""
        return OperationJournal(journal.path).claim("123", "race", "send_msg", {"a": 1})

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(attempt, range(10)))
    assert sum(result["claimed"] for result in results) == 1
    assert all(result["state"] == "pending" and not result["conflict"] for result in results)


def test_complete_requires_existing_claim(journal):
    """Completion never inserts fabricated claims or successes."""
    with pytest.raises(ValueError, match="not been claimed"):
        journal.complete("123", "missing", {"status": "ok"})
    assert journal.claim("123", "missing", "send_msg", {})["claimed"]


@pytest.mark.parametrize("value", [None, 1, True, "", "0", "000", "-1", "+1", " 1", "１", "١"])
def test_invalid_accounts_rejected(journal, value):
    """Only nonzero ASCII decimal strings can scope operations."""
    with pytest.raises(ValueError):
        journal.claim(value, "op", "send_msg", {})


@pytest.mark.parametrize("value", [None, True, "", "x" * 257, "op\n", "op\x00", "op\x7f", "op\x85", "\ud800"])
def test_invalid_operation_ids_rejected(journal, value):
    """Opaque IDs preserve spelling but reject controls and invalid UTF-8."""
    with pytest.raises(ValueError):
        journal.claim("123", value, "send_msg", {})


@pytest.mark.parametrize("value", [None, 1, "", "1send", ".", "..", "send/msg", "send-msg", "中文", "x" * 257])
def test_invalid_actions_rejected(journal, value):
    """Actions cannot introduce whitespace, paths or non-ASCII names."""
    with pytest.raises(ValueError):
        journal.claim("123", "op", value, {})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"),
                                   {1: "alias"}, {"a": [{False: 1}]},
                                   {"a": object()}, {"a": {1, 2}}, "\ud800"])
def test_unsupported_json_rejected(value):
    """Canonical JSON rejects non-finite, lossy-key and non-UTF-8 input."""
    with pytest.raises(ValueError):
        canonical_json(value)


def test_copy_json_and_tuples_use_single_canonical_implementation():
    """Tuples follow stdlib JSON arrays, and detached values share no state."""
    original = {"b": (True, None), "a": ["中文"]}
    assert canonical_json(original) == '{"a":["中文"],"b":[true,null]}'
    detached = copy_json(original)
    detached["a"].append("changed")
    assert original["a"] == ["中文"]
    assert detached["b"] == [True, None]
    assert json.loads(canonical_json(original))["b"] == [True, None]


def test_cycles_are_value_errors():
    """Circular structures fail safely rather than recurse indefinitely."""
    cyclic = []
    cyclic.append(cyclic)
    with pytest.raises(ValueError):
        canonical_json(cyclic)


def test_serializer_recursion_error_is_normalized(monkeypatch):
    """Normalize interpreter-dependent depth failures without a fixed limit."""
    def too_deep(*_args, **_kwargs):
        """Represent a stdlib serializer rejecting its recursion depth."""
        raise RecursionError("too deep")

    monkeypatch.setattr("qq_transport.store.json.dumps", too_deep)
    with pytest.raises(ValueError):
        canonical_json({})


@pytest.mark.parametrize("value", [None, [], "text", True])
def test_params_and_responses_must_be_objects(journal, value):
    """Both protocol envelopes require JSON objects, not arbitrary JSON."""
    with pytest.raises(ValueError):
        journal.claim("123", "op", "send_msg", value)
    journal.claim("123", "op", "send_msg", {})
    with pytest.raises(ValueError):
        journal.complete("123", "op", value)
    assert journal.claim("123", "op", "send_msg", {})["state"] == "pending"


@pytest.mark.parametrize("value", ["", ":memory:"])
def test_explicit_durable_path_required(value):
    """Per-call connections must not silently discard in-memory claims."""
    with pytest.raises(ValueError):
        OperationJournal(value)
