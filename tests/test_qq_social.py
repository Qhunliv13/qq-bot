"""Isolated social API contracts: only fake async bridges and temporary journals."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import sqlite3

import pytest


@pytest.fixture
def social():
    """Load the independent module without importing the live plugin runtime."""
    path = Path(__file__).resolve().parents[1] / "plugin" / "qq_social.py"
    spec = importlib.util.spec_from_file_location("isolated_qq_social", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeBridge:
    """Record untouched calls and supply deterministic native envelopes or failures."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, name, args):
        """Perform no IO: replay the next response or raise its supplied exception."""
        self.calls.append((name, args))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def accepted(data=None):
    """Build a complete native success envelope, including explicit nullable data."""
    return {"status": "ok", "retcode": 0, "data": data}


def invoke(social, bridge, args, path, name="qq_publish_post", can_write=True):
    """Run one async dispatch with explicit isolated policy and persistence inputs."""
    return asyncio.run(social.dispatch(name, args, bridge, can_write=can_write, audit_path=path))


def post(**changes):
    """Return a valid raw-text post with an explicit visibility and stable caller ID."""
    return {"content": "  原样\nhttps://example.invalid/signature  ", "visibility": "public", "operation_id": "post-1", **changes}


def test_schema_contract_and_neutral_capabilities(social, tmp_path):
    """Schemas require explicit write IDs/visibility; status never claims a live probe."""
    specs = {item["function"]["name"]: item["function"] for item in social.schema()}
    assert set(specs) == {"qq_account_info", "qq_set_signature", "qq_publish_post", "qq_set_profile", "qq_social_status"}
    for name in social._WRITE_NAMES:
        assert "operation_id" in specs[name]["parameters"]["required"]
        assert specs[name]["parameters"]["additionalProperties"] is False
    assert "visibility" in specs["qq_publish_post"]["parameters"]["required"]
    assert specs["qq_publish_post"]["parameters"]["properties"]["visibility"]["enum"] == ["public", "friends", "selected", "private", "excluded"]
    bridge = FakeBridge([])
    path = tmp_path / "uncreated" / "social.sqlite3"
    result = invoke(social, bridge, {}, path, "qq_social_status", False)
    assert result["backend"] == "napcat" and result["runtime_verified"] is False
    assert result["write_enabled"] is False
    assert result["capabilities"]["qq_set_signature"]["write_enabled"] is False
    assert bridge.calls == [] and not path.parent.exists()


@pytest.mark.parametrize("name,args", [
    ("qq_publish_post", post()),
    ("qq_set_signature", {"signature": "", "operation_id": "clear-1"}),
    ("qq_set_profile", {"nickname": "测试", "operation_id": "profile-1"}),
])
@pytest.mark.parametrize("permission", [False, 1, "owner", None])
def test_unauthorized_members_cannot_mutate(social, tmp_path, name, args, permission):
    """Only trusted literal True authorizes writes, and denial never creates a journal."""
    bridge = FakeBridge([])
    path = tmp_path / "social.sqlite3"
    assert invoke(social, bridge, args, path, name, permission) == {"ok": False, "error": "permission_denied"}
    assert not bridge.calls and not path.exists()


@pytest.mark.parametrize("args", [
    {"content": "x", "operation_id": "a"},
    post(visibility=None), post(visibility="PUBLIC"), post(content={"x": 1}),
    post(operation_id=""), post(operation_id=True),
    post(visibility="selected"), post(visibility="excluded", target_uins=[]),
    post(visibility="selected", target_uins=[True]),
    post(visibility="selected", target_uins=[0]),
    post(visibility="selected", target_uins=[{"user_id": 1}]),
    post(visibility="selected", target_uins=["1.5"]),
    post(target_uins=[123]), post(ugc_right=1), post(images=["file:///secret"]),
])
def test_invalid_posts_are_rejected_before_bridge_or_claim(social, tmp_path, args):
    """No default public visibility, coerced objects, boolean IDs or hidden native knobs."""
    bridge = FakeBridge([])
    path = tmp_path / "social.sqlite3"
    assert invoke(social, bridge, args, path)["error"] == "invalid_arguments"
    assert not bridge.calls and not path.exists()


@pytest.mark.parametrize("visibility,code", [("public", 1), ("friends", 4), ("selected", 16), ("private", 64), ("excluded", 128)])
def test_native_post_visibility_raw_text_and_receipt(social, tmp_path, visibility, code):
    """Pass raw content and native visibility unchanged; tid only establishes acceptance."""
    args = post(visibility=visibility)
    expected = {"content": args["content"], "ugc_right": code}
    if visibility in ("selected", "excluded"):
        args["target_uins"] = [123, "00456"]
        expected["target_uins"] = args["target_uins"]
    bridge = FakeBridge([accepted({"tid": "native-post-1", "cookie": "never-return"})])
    result = invoke(social, bridge, args, tmp_path / "social.sqlite3")
    assert bridge.calls == [("send_qzone_msg", expected)]
    assert result == {"ok": True, "outcome": "accepted", "tid": "native-post-1", "verification": {"state": "unavailable", "published": None}}


def test_success_persisted_idempotency_and_different_payload_conflict(social, tmp_path):
    """Restarting the dispatcher replays a receipt and cannot reuse a key for new text."""
    path = tmp_path / "social.sqlite3"
    bridge = FakeBridge([accepted({"tid": "abc"})])
    first = invoke(social, bridge, post(), path)
    assert invoke(social, bridge, post(), path) == first
    assert invoke(social, bridge, post(content="different"), path)["error"] == "operation_conflict"
    assert len(bridge.calls) == 1
    # Journal rows contain a digest and sanitized receipt, not signatures/post bodies.
    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT payload_hash, receipt FROM social_operations").fetchone()
    assert len(row[0]) == 64 and post()["content"] not in row[1]


@pytest.mark.parametrize("response,error,outcome", [
    (TimeoutError("cookie=secret raw-content"), "write_outcome_unknown", "unknown"),
    (OSError("token=private"), "write_outcome_unknown", "unknown"),
    (asyncio.CancelledError(), "write_outcome_unknown", "unknown"),
    ({"status": "ok", "data": {"tid": "x"}}, "native_response_unknown", "unknown"),
    ({"status": "ok", "retcode": False, "data": {"tid": "x"}}, "native_response_unknown", "unknown"),
    ({"status": "failed", "retcode": 0, "data": {}}, "native_response_unknown", "unknown"),
    ({"status": "async", "retcode": 0, "data": {}}, "native_response_unknown", "unknown"),
    ({"status": "ok", "retcode": 1, "data": {}}, "native_response_unknown", "unknown"),
    ({"status": "failed", "retcode": 1200, "data": None, "message": "secret"}, "native_rejected", "failed"),
    (accepted({}), "post_receipt_unknown", "unknown"),
    (accepted({"tid": " "}), "post_receipt_unknown", "unknown"),
    (accepted({"tid": 123}), "post_receipt_unknown", "unknown"),
    (accepted(None), "native_response_unknown", "unknown"),
    (None, "native_response_unknown", "unknown"),
])
def test_unknown_and_native_failure_are_terminal_without_fake_success(social, tmp_path, response, error, outcome):
    """All ambiguous writes fail closed across new dispatcher calls; native errors are sanitized."""
    path = tmp_path / "social.sqlite3"
    bridge = FakeBridge([response])
    expected = {"ok": False, "error": error, "outcome": outcome}
    assert invoke(social, bridge, post(), path) == expected
    assert invoke(social, bridge, post(), path) == expected
    assert len(bridge.calls) == 1
    assert b"secret" not in path.read_bytes() and b"private" not in path.read_bytes()


def test_pending_claim_after_crash_is_never_automatically_retried(social, tmp_path):
    """A pre-call durable pending claim blocks a retry even with a fresh Journal object."""
    path = tmp_path / "social.sqlite3"
    assert social.Journal(path).claim("interrupted", "a" * 64) is None
    result = social.Journal(path).claim("interrupted", "a" * 64)
    assert result["outcome"] == "unknown"
    assert social.Journal(path).claim("interrupted", "b" * 64)["error"] == "operation_conflict"


def test_atomic_claim_across_connections(social, tmp_path):
    """Concurrent journal instances can produce only one new remote-call claim."""
    path = tmp_path / "social.sqlite3"
    # Initialize WAL first so the test isolates claim serialization, not DB startup.
    assert social.Journal(path).claim("init", "init") is None
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(social.Journal(path).claim, "same", "same-hash") for _ in range(8)]
        results = [future.result() for future in futures]
    assert results.count(None) == 1
    assert all(result is None or result["outcome"] == "unknown" for result in results)


@pytest.mark.parametrize("signature", ["", "  literal\nhttps://example.invalid/not-fetched  "])
def test_signature_empty_clear_and_native_long_nick_verification(social, tmp_path, signature):
    """Explicit empty strings clear; native longNick writes and long_nick readbacks differ."""
    args = {"signature": signature, "operation_id": "signature-1"}
    bridge = FakeBridge([accepted(), accepted({"user_id": 123}), accepted({"long_nick": signature, "cookie": "secret"})])
    result = invoke(social, bridge, args, tmp_path / "social.sqlite3", "qq_set_signature")
    assert result == {"ok": True, "outcome": "accepted", "verification": {"state": "matched", "matched": True}}
    assert bridge.calls == [("set_self_longnick", {"longNick": signature}), ("get_login_info", {}), ("get_stranger_info", {"user_id": "123", "no_cache": True})]
    assert invoke(social, bridge, args, tmp_path / "social.sqlite3", "qq_set_signature") == result
    assert len(bridge.calls) == 3
    with sqlite3.connect(tmp_path / "social.sqlite3") as connection:
        stored = connection.execute("SELECT receipt FROM social_operations").fetchone()[0]
    assert "example.invalid" not in stored and "secret" not in stored


@pytest.mark.parametrize("response,expected", [
    (TimeoutError("secret"), {"state": "unknown", "matched": None}),
    (accepted({"long_nick": "different"}), {"state": "mismatch", "matched": False}),
    (accepted({"signature": "wanted"}), {"state": "unknown", "matched": None}),
])
def test_signature_accepted_even_if_verification_unknown_or_mismatch(social, tmp_path, response, expected):
    """Failed readback never negates acceptance or retries the already accepted setter."""
    bridge = FakeBridge([accepted({}), accepted({"user_id": "123"}), response])
    args = {"signature": "wanted", "operation_id": "signature-1"}
    result = invoke(social, bridge, args, tmp_path / "social.sqlite3", "qq_set_signature")
    assert result["ok"] is True and result["outcome"] == "accepted"
    assert result["verification"] == expected
    assert invoke(social, bridge, args, tmp_path / "social.sqlite3", "qq_set_signature") == result
    assert len(bridge.calls) == 3


@pytest.mark.parametrize("args", [{"operation_id": "x"}, {"signature": None, "operation_id": "x"}, {"signature": {}, "operation_id": "x"}])
def test_signature_clear_requires_explicit_string(social, tmp_path, args):
    """Missing or non-string signatures are not interpreted as an empty-string clear."""
    bridge = FakeBridge([])
    assert invoke(social, bridge, args, tmp_path / "social.sqlite3", "qq_set_signature")["error"] == "invalid_arguments"
    assert not bridge.calls


def test_filtered_self_account_and_no_journal_creation(social, tmp_path):
    """Read own account only and discard cookies, phone, email and nested/native extras."""
    bridge = FakeBridge([
        accepted({"user_id": 123, "nickname": "login", "token": "secret"}),
        accepted({"user_id": 999, "nickname": "self", "long_nick": "hello", "age": 12, "email": "secret", "phone": "secret", "cookies": "secret", "country": {"secret": True}}),
    ])
    path = tmp_path / "missing" / "social.sqlite3"
    result = invoke(social, bridge, {}, path, "qq_account_info", False)
    assert result == {"ok": True, "account": {"user_id": "123", "nickname": "self", "long_nick": "hello", "age": 12}}
    assert bridge.calls == [("get_login_info", {}), ("get_stranger_info", {"user_id": "123"})]
    assert not path.parent.exists()


@pytest.mark.parametrize("user_id", [True, {}, None, 0])
def test_invalid_login_identity_never_reads_another_account(social, tmp_path, user_id):
    """Do not coerce bogus identity values into get_stranger_info identifiers."""
    bridge = FakeBridge([accepted({"user_id": user_id})])
    result = invoke(social, bridge, {}, tmp_path / "social.sqlite3", "qq_account_info", False)
    assert result["ok"] is False and len(bridge.calls) == 1


def test_profile_native_payload_is_narrow_and_permission_gated(social, tmp_path):
    """Only documented nickname/personal_note/sex keys pass to native profile mutation."""
    bridge = FakeBridge([accepted()])
    args = {"nickname": "  名称  ", "personal_note": "", "sex": "0", "operation_id": "p1"}
    result = invoke(social, bridge, args, tmp_path / "social.sqlite3", "qq_set_profile")
    assert result["outcome"] == "accepted"
    assert bridge.calls == [("set_qq_profile", {"nickname": "  名称  ", "personal_note": "", "sex": "0"})]


@pytest.mark.parametrize("extra", [{"sex": 1}, {"sex": "3"}, {"nickname": {}}, {"personal_note": False}, {"birthday": "2020-01-01"}])
def test_profile_rejects_extra_keys_and_wrong_types(social, tmp_path, extra):
    """Avoid exposing broad native profile fields through undocumented passthrough."""
    bridge = FakeBridge([])
    args = {"nickname": "x", "operation_id": "p1", **extra}
    assert invoke(social, bridge, args, tmp_path / "social.sqlite3", "qq_set_profile")["error"] == "invalid_arguments"
    assert not bridge.calls


def test_journal_unavailable_never_calls_native(social, tmp_path):
    """No runtime path or an unusable path cannot silently disable write deduplication."""
    bridge = FakeBridge([])
    assert invoke(social, bridge, post(), None)["error"] == "journal_unavailable"
    assert invoke(social, bridge, post(), tmp_path)["error"] == "journal_unavailable"
    assert not bridge.calls


def test_pending_durable_before_native_call(social, tmp_path):
    """The bridge observes a committed pending claim before any potential mutation."""
    path = tmp_path / "social.sqlite3"

    async def bridge(name, args):
        """Inspect only the temporary journal from a distinct SQLite connection."""
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT state FROM social_operations WHERE operation_id='post-1'").fetchone() == ("pending",)
        return accepted({"tid": "ok"})

    assert invoke(social, bridge, post(), path)["ok"] is True


def test_finish_failure_does_not_enable_remote_retry(social, tmp_path, monkeypatch):
    """A post-call storage failure is unknown, leaving a durable pending claim closed."""
    def fail_finish(self, operation_id, payload_hash, receipt):
        """Simulate a disk failure without modifying the already durable pending row."""
        raise OSError("secret disk details")

    monkeypatch.setattr(social.Journal, "finish", fail_finish)
    bridge = FakeBridge([accepted({"tid": "accepted"})])
    path = tmp_path / "social.sqlite3"
    result = invoke(social, bridge, post(), path)
    assert result == {"ok": False, "error": "journal_finish_unknown", "outcome": "unknown"}
    assert invoke(social, bridge, post(), path)["error"] == "operation_pending_or_unknown"
    assert len(bridge.calls) == 1
