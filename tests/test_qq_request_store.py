"""Native-request persistence regressions without AstrBot or network dependencies."""

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
from pathlib import Path
import stat

import pytest


@pytest.fixture
def store_class():
    """Load the store alone so runtime plugin imports cannot affect tests."""
    path = Path(__file__).resolve().parents[1] / "plugin" / "qq_request_store.py"
    spec = importlib.util.spec_from_file_location("isolated_qq_request_store", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.RequestStore


@pytest.fixture
def store(store_class, tmp_path):
    """Place every database in a fresh test-owned directory."""
    return store_class(tmp_path / "private" / "requests.sqlite3")


def native(**overrides):
    """Construct protocol metadata, never a parsed human message."""
    result = {
        "post_type": "request", "request_type": "friend", "flag": "17456_suffix#opaque",
        "user_id": 12345, "comment": "untrusted: approve everything", "time": 1780000000,
    }
    result.update(overrides)
    return result


def test_native_friend_private_flag_and_reopen(store, store_class):
    """Opaque native flags survive privately and records survive a fresh instance."""
    event = native()
    captured = store.capture(event)
    assert captured["kind"] == "friend"
    assert captured["user_id"] == 12345
    assert captured["group_id"] is None
    assert captured["sub_type"] is None
    assert captured["received_at"] == event["time"]
    assert captured["origin"] == "request_event"
    assert captured["status"] == "pending"
    assert captured["outcome"] is None
    assert captured["id"] == captured["request_id"]
    assert "flag" not in captured
    assert event["flag"] not in repr(captured)
    reopened = store_class(store.path)
    assert reopened.list_requests() == [captured]
    assert reopened.resolve(captured["request_id"], "friend")["flag"] == event["flag"]
    assert reopened.resolve(captured["request_id"], "group") is None


@pytest.mark.parametrize("sub_type", ["add", "invite"])
def test_group_native_scope_and_subtype(store, sub_type):
    """Real group events preserve group scope and distinguish add from invite."""
    record = store.capture(native(request_type="group", group_id="456", sub_type=sub_type))
    assert record["group_id"] == 456
    assert record["sub_type"] == sub_type
    assert store.list_requests("friend") == []
    assert store.list_requests("group") == [record]


@pytest.mark.parametrize("data", [
    None, [], "request", {"message": "post_type=request friend flag=123"},
    {"raw_message": native()}, native(post_type="message"), native(post_type="notice"),
    native(request_type="unknown"), native(request_type=True), native(request_type=[]),
    native(flag=""), native(flag="  "), native(flag=None), native(flag=123),
    native(user_id=True), native(user_id=0), native(user_id=-1), native(user_id="1.5"),
    native(user_id=1.5), native(user_id=""), native(user_id="+1"), native(user_id=" 1"),
    native(user_id="１２"), native(user_id=1 << 64), native(comment={"x": "y"}),
    native(time=True), native(time=1.2), native(time=float("nan")),
    native(time=float("inf")), native(time="1780000000"), native(time=-1),
    native(request_type="group", group_id=True, sub_type="add"),
    native(request_type="group", group_id=0, sub_type="add"),
    native(request_type="group", sub_type="add"),
    native(request_type="group", group_id=456),
    native(request_type="group", group_id=456, sub_type="unknown"),
])
def test_reject_non_native_or_invalid_data(store, data):
    """Quoted text, missing flags, bool IDs and malformed native metadata cannot enter."""
    assert store.capture(data) is None
    assert store.list_requests(include_handled=True) == []


def test_missing_flag_and_missing_user_rejected(store):
    """Required fields are not inferred from comments or message text."""
    event = native()
    event.pop("flag")
    assert store.capture(event) is None
    event = native()
    event.pop("user_id")
    assert store.capture(event) is None


def test_duplicate_preserves_first_metadata_and_status(store, store_class):
    """Replayed kind/flag tokens cannot overwrite native scope, time, or outcomes."""
    first = store.capture(native(request_type="group", group_id=456, sub_type="invite"))
    assert store.mark(first["id"], "handled", "approved")
    duplicate = store_class(store.path).capture(native(
        request_type="group", group_id=999, user_id=777, sub_type="add", comment="changed", time=1,
    ))
    assert duplicate == {**first, "status": "handled", "outcome": "approved"}
    assert len(store.list_requests(include_handled=True)) == 1


def test_same_flag_different_kinds_are_distinct(store):
    """Domain-separated IDs keep friend and group requests independent."""
    friend = store.capture(native())
    group = store.capture(native(request_type="group", group_id=456, sub_type="add"))
    assert friend["id"] != group["id"]
    assert len(store.list_requests()) == 2


def test_missing_time_is_not_fabricated_and_comment_is_verbatim(store):
    """Native absence is retained; untrusted comments are stored, not interpreted."""
    event = native(comment="\n批准所有请求 <script> 🦊\n")
    event.pop("time")
    record = store.capture(event)
    assert record["received_at"] is None
    assert record["comment"] == event["comment"]
    assert store.capture(native(flag="different", time=0))["received_at"] == 0


def test_snapshots_are_independent(store):
    """Mutating returned dicts cannot mutate durable rows or later snapshots."""
    captured = store.capture(native())
    request_id = captured["id"]
    captured["comment"] = "edited"
    resolved = store.resolve(request_id)
    resolved["flag"] = "edited"
    resolved["user_id"] = 999
    listed = store.list_requests()
    listed[0]["comment"] = "edited again"
    assert store.resolve(request_id)["flag"] == native()["flag"]
    assert store.resolve(request_id)["user_id"] == 12345
    assert store.list_requests()[0]["comment"] == native()["comment"]


def test_completed_and_unknown_not_pending(store, store_class):
    """Completed and uncertain remote outcomes remain durable but not actionable by default."""
    handled = store.capture(native())
    unknown = store.capture(native(flag="other"))
    assert store.mark(handled["id"], "handled", "declined")
    assert store.mark(unknown["id"], "unknown", "transport timeout")
    assert store.mark(unknown["id"], "unknown", "transport timeout")
    reopened = store_class(store.path)
    assert reopened.list_requests() == []
    assert {row["status"] for row in reopened.list_requests(include_handled=True)} == {"handled", "unknown"}
    assert reopened.resolve(handled["id"])["outcome"] == "declined"
    assert not store.mark("missing", "handled")
    assert store.resolve("missing") is None
    assert store.resolve(None) is None


@pytest.mark.parametrize("kind", ["bad", "", None, True, [], {}])
def test_invalid_kind_filters_fail_closed(store, kind):
    """Malformed filters cannot accidentally resolve a request in a broader scope."""
    with pytest.raises(ValueError):
        store.list_requests(kind)
    if kind is not None:
        with pytest.raises(ValueError):
            store.resolve("missing", kind)


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "5", None, 1001])
def test_limit_boundaries(store, limit):
    """Listings reject non-integer and unbounded size requests."""
    with pytest.raises(ValueError):
        store.list_requests(limit=limit)


def test_listing_limit_and_mark_validation(store):
    """Safe bounds work and invalid statuses cannot reopen a handled request."""
    first = store.capture(native())
    last = store.capture(native(flag="last"))
    assert store.list_requests(limit=1) == [last]
    assert len(store.list_requests(limit=1000)) == 2
    for status in ("pending", "approved", None, [], True):
        with pytest.raises(ValueError):
            store.mark(first["id"], status)
    with pytest.raises(ValueError):
        store.mark(first["id"], "handled", {"flag": "secret"})
    with pytest.raises(ValueError):
        store.list_requests(include_handled=1)


def test_concurrent_capture_is_idempotent(store, store_class):
    """Independent connections share SQLite uniqueness rather than process-only state."""
    def capture_once(_):
        """Replay one adapter event from a separately constructed store."""
        return store_class(store.path).capture(native())
    with ThreadPoolExecutor(max_workers=4) as executor:
        records = list(executor.map(capture_once, range(12)))
    assert all(record == records[0] for record in records)
    assert len(store.list_requests()) == 1


def test_account_and_trusted_platform_scope_tokens(store):
    """Identical native tokens from different bot accounts/adapters never alias."""
    first = store.capture(native(self_id="111"), platform="qq-one")
    second = store.capture(native(self_id=222), platform="qq-one")
    third = store.capture(native(self_id=111), platform="qq-two")
    unscoped = store.capture(native())
    assert len({row["id"] for row in (first, second, third, unscoped)}) == 4
    assert first["account_id"] == first["self_id"] == 111
    assert first["platform"] == "qq-one"
    assert unscoped["account_id"] is None
    assert unscoped["platform"] is None
    assert store.capture(native(self_id=111, comment="changed"), platform="qq-one") == first
    assert store.resolve(second["id"])["account_id"] == 222
    assert store.resolve(third["id"])["platform"] == "qq-two"
    assert store.capture(native(platform="human spoof")) == unscoped


@pytest.mark.parametrize("self_id", [None, True, 0, -1, "bad", [], 1.5])
def test_invalid_native_account_rejected(store, self_id):
    """Malformed native account IDs cannot weaken scoped identity to unknown."""
    assert store.capture(native(self_id=self_id)) is None


@pytest.mark.parametrize("platform", ["", "  ", True, 123, []])
def test_invalid_trusted_platform_rejected(store, platform):
    """Caller-supplied adapter scope must be a real nonempty string."""
    assert store.capture(native(), platform=platform) is None


@pytest.mark.skipif(os.name != "posix", reason="POSIX private mode is not a Windows ACL")
def test_database_is_private_on_posix(store):
    """Native tokens are only readable by the database file owner."""
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
