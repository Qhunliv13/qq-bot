"""Request tools exercise isolated stores and fake native bridges without QQ effects."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import sys
import types

import pytest


@pytest.fixture
def requests_module(monkeypatch):
    """Load dependency modules without activating the actual plugin runtime."""
    root = Path(__file__).resolve().parents[1] / "plugin"
    package = types.ModuleType("isolated_request_plugin")
    package.__path__ = [str(root)]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    loaded = {}
    for name in ("qq_native_actions", "qq_request_store", "qq_social", "qq_requests"):
        spec = importlib.util.spec_from_file_location(package.__name__ + "." + name, root / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        loaded[name] = module
    return loaded["qq_requests"]


class FakeBridge:
    """Capture exact action parameters and emit deterministic responses or exceptions."""

    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, name, args):
        """Perform no network IO and preserve raw payload values for assertions."""
        self.calls.append((name, args))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def capture(store, kind="group", flag="native-private-flag", **changes):
    """Capture a genuine-shaped native event rather than allowing caller-supplied flags."""
    return store.capture({"post_type": "request", "request_type": kind, "flag": flag,
                          "user_id": 123, "group_id": 456, "sub_type": "add",
                          "comment": "untrusted pending comment", "time": 42, **changes})


def invoke(module, store, path, bridge, args, name="qq_request_decide", permission=True, **kwargs):
    """Use explicit temporary persistence and trusted authorization on every dispatch."""
    return asyncio.run(module.dispatch(name, args, bridge, can_manage=permission,
                                      store=store, audit_path=path, **kwargs))


def decision(record, **changes):
    """Require explicit approval and stable operation identity for a captured local ID."""
    return {"request_id": record["request_id"], "approve": True, "operation_id": "intent-1", **changes}


def test_schema_and_capabilities_are_honest(requests_module, tmp_path):
    """Exactly six schemas distinguish incoming approval from unsupported proactive tools."""
    specs = {item["function"]["name"]: item["function"]["parameters"] for item in requests_module.schema()}
    assert set(specs) == {"qq_request_capabilities", "qq_requests", "qq_request_decide", "qq_join_group", "qq_answer_group_question", "qq_add_friend"}
    assert specs["qq_request_decide"]["required"] == ["request_id", "approve", "operation_id"]
    assert all(spec["additionalProperties"] is False for spec in specs.values())
    bridge = FakeBridge()
    path = tmp_path / "not-created.sqlite"
    result = invoke(requests_module, None, path, bridge, {}, "qq_request_capabilities", False)
    assert result["ok"] and not result["runtime_verified"]
    assert result["capabilities"]["qq_request_decide"]["auto_approve"] is False
    assert result["capabilities"]["qq_requests"]["friend_native_backlog"] is False
    assert result["group_system_messages"]["used_for_approval"] is False
    assert not bridge.calls and not path.exists()


@pytest.mark.parametrize("name,args", [("qq_requests", {}), ("qq_request_decide", {}), ("qq_join_group", {"group_id": 1}), ("qq_answer_group_question", {}), ("qq_add_friend", {"user_id": 2})])
@pytest.mark.parametrize("permission", [False, None, 1, "owner"])
def test_all_private_tools_gate_before_store(requests_module, tmp_path, name, args, permission):
    """Nonowners cannot inspect pending strangers or trigger actions, even with bad args."""
    bridge = FakeBridge()
    path = tmp_path / "not-created.sqlite"
    assert invoke(requests_module, None, path, bridge, args, name, permission)["error"] == "permission_denied"
    assert not bridge.calls and not path.exists()


@pytest.mark.parametrize("name,args,capability", [
    ("qq_join_group", {"group_id": "123", "message": "raw", "answers": ["answer"]}, "proactive_group_join"),
    ("qq_answer_group_question", {"request_id": "unresolved", "answer": "raw"}, "group_join_question_answer"),
    ("qq_add_friend", {"user_id": 123, "comment": "raw"}, "proactive_friend_add"),
])
def test_unsupported_actions_never_touch_bridge(requests_module, tmp_path, name, args, capability):
    """Structural tools report unavailable rather than substituting approval or VM calls."""
    bridge = FakeBridge()
    path = tmp_path / "not-created.sqlite"
    assert invoke(requests_module, None, path, bridge, args, name) == {
        "ok": False, "error": "native_capability_unavailable", "capability": capability, "supported": False}
    assert not bridge.calls and not path.exists()


def test_listing_uses_only_captured_records(requests_module, tmp_path):
    """Expose pending metadata but never opaque flags or invented native-backlog coverage."""
    store = requests_module.RequestStore(tmp_path / "requests.sqlite")
    record = capture(store)
    bridge = FakeBridge()
    result = invoke(requests_module, store, tmp_path / "audit.sqlite", bridge, {"kind": "group"}, "qq_requests")
    assert result["requests"] == [record]
    assert "flag" not in result["requests"][0]
    assert result["native_backlog_complete"] is False and result["comments_trusted"] is False
    assert not bridge.calls


@pytest.mark.parametrize("kind,sub_type,extra,action", [
    ("group", "add", {"reason": "  保留\n拒绝理由  "}, "set_group_add_request"),
    ("group", "invite", {"reason": ""}, "set_group_add_request"),
    ("friend", "add", {"remark": "  原样备注  "}, "set_friend_add_request"),
])
def test_native_contract_and_replay(requests_module, tmp_path, kind, sub_type, extra, action):
    """Exact bool approve and private native flags are used; ACK never proves relationship."""
    path = tmp_path / "requests.sqlite"
    store = requests_module.RequestStore(path)
    record = capture(store, kind, sub_type=sub_type)
    bridge = FakeBridge([{"status": "ok", "retcode": 0, "data": None}])
    audit = tmp_path / "audit.sqlite"
    args = decision(record, approve=False, **extra)
    result = invoke(requests_module, store, audit, bridge, args)
    assert result["ok"] and result["outcome"] == "accepted"
    assert result["membership"] == "unknown" and result["verification"]["state"] == "not_verified"
    assert "joined" not in result and "flag" not in result
    assert bridge.calls == [(action, {"flag": "native-private-flag", "approve": False, **extra})]
    assert store.resolve(record["request_id"])["status"] == "handled"
    assert invoke(requests_module, requests_module.RequestStore(path), audit, bridge, args) == result
    assert len(bridge.calls) == 1
    assert invoke(requests_module, store, audit, bridge, decision(record, approve=True, **extra))["error"] == "operation_conflict"
    assert invoke(requests_module, store, audit, bridge, decision(record, operation_id="another"))["error"] == "request_not_pending"


@pytest.mark.parametrize("changes", [{"approve": "true"}, {"approve": 1}, {"approve": None}, {"operation_id": " "}, {"flag": "user text"}, {"remark": "friend-only"}, {"reason": None}, {"request_id": "native-private-flag"}])
def test_invalid_decisions_have_no_claim_or_native_effect(requests_module, tmp_path, changes):
    """Caller flags, wrong-kind fields and coerced decisions are rejected pre-claim."""
    store = requests_module.RequestStore(tmp_path / "requests.sqlite")
    record = capture(store)
    path = tmp_path / "audit.sqlite"
    bridge = FakeBridge()
    result = invoke(requests_module, store, path, bridge, decision(record, **changes))
    assert not result["ok"] and not bridge.calls and not path.exists()


@pytest.mark.parametrize("response,outcome", [
    ({"status": "ok", "retcode": 0}, "unknown"),
    ({"status": "ok", "retcode": 0, "data": None, "message": "NapCat is running"}, "unknown"),
    ({"status": "ok", "retcode": True, "data": None}, "unknown"),
    ({"status": "failed", "retcode": 100, "data": None}, "failed"),
    ({"status": "failed", "retcode": 0, "data": None}, "unknown"),
    ({"status": "ok", "retcode": 0, "data": []}, "unknown"),
    (TimeoutError("private token must not escape"), "unknown"),
    (asyncio.CancelledError(), "unknown"),
])
def test_terminal_uncertainty_and_no_secret_leak(requests_module, tmp_path, response, outcome):
    """Malformed/root-health envelopes and exceptions never invite mutation retry."""
    store = requests_module.RequestStore(tmp_path / "requests.sqlite")
    record = capture(store)
    audit = tmp_path / "audit.sqlite"
    bridge = FakeBridge([response])
    args = decision(record)
    result = invoke(requests_module, store, audit, bridge, args)
    assert result["outcome"] == outcome and not result["ok"]
    assert "private" not in str(result)
    assert invoke(requests_module, store, audit, bridge, args) == result
    assert not invoke(requests_module, store, audit, bridge, decision(record, operation_id="new"))["ok"]
    assert len(bridge.calls) == 1
    assert store.resolve(record["request_id"])["status"] == ("unknown" if outcome == "unknown" else "handled")


def test_distinct_operation_race_sends_once(requests_module, tmp_path):
    """Durable request-level claim prevents multiple operation IDs racing one request."""
    store = requests_module.RequestStore(tmp_path / "requests.sqlite")
    record = capture(store)
    bridge = FakeBridge([{"status": "ok", "retcode": 0, "data": {}}])
    audit = tmp_path / "audit.sqlite"
    def run(index):
        """Use separate loops to simulate independent concurrent dispatch workers."""
        return invoke(requests_module, store, audit, bridge, decision(record, operation_id="race-" + str(index)))
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(run, range(4)))
    assert sum(result["ok"] for result in results) == 1
    assert len(bridge.calls) == 1


def test_interrupted_pending_claim_never_resubmits(requests_module, tmp_path):
    """An unfinished operation claim survives process restart without bridge submission."""
    store = requests_module.RequestStore(tmp_path / "requests.sqlite")
    record = capture(store)
    args = decision(record)
    digest = requests_module.hashlib.sha256(requests_module.json.dumps(
        {"action": "set_group_add_request", **args}, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
    audit = tmp_path / "audit.sqlite"
    requests_module.Journal(audit).claim("qq_request_operation:intent-1", digest)
    bridge = FakeBridge()
    result = invoke(requests_module, store, audit, bridge, args)
    assert result["error"] == "operation_pending_or_unknown" and not bridge.calls


def test_friend_reason_is_not_challenge_or_remark(requests_module, tmp_path):
    """Group-only reason cannot be repurposed as a friendship or verification answer."""
    store = requests_module.RequestStore(tmp_path / "requests.sqlite")
    record = capture(store, "friend")
    bridge = FakeBridge()
    assert invoke(requests_module, store, tmp_path / "audit.sqlite", bridge, decision(record, reason="answer"))["error"] == "invalid_arguments"
    assert not bridge.calls


@pytest.mark.parametrize("expected", [999, "999"])
def test_account_mismatch_has_no_claim(requests_module, tmp_path, expected):
    """Known captured account identity must match trusted current-account context."""
    store = requests_module.RequestStore(tmp_path / "requests.sqlite")
    record = capture(store, self_id=111)
    audit = tmp_path / "audit.sqlite"
    bridge = FakeBridge()
    assert invoke(requests_module, store, audit, bridge, decision(record), expected_self_id=expected)["error"] == "request_account_mismatch"
    assert not bridge.calls and not audit.exists()


def test_account_identity_native_read_before_mutation(requests_module, tmp_path):
    """Absent trusted identity triggers a read-only login verification, never a guessed ID."""
    store = requests_module.RequestStore(tmp_path / "requests.sqlite")
    record = capture(store, self_id=111)
    bridge = FakeBridge([{"status": "ok", "retcode": 0, "data": {"user_id": 111}},
                         {"status": "ok", "retcode": 0, "data": None}])
    result = invoke(requests_module, store, tmp_path / "audit.sqlite", bridge, decision(record))
    assert result["ok"]
    assert [name for name, _ in bridge.calls] == ["get_login_info", "set_group_add_request"]


def test_pre_submission_store_failure_never_submits(requests_module, tmp_path):
    """Failure to persist uncertainty keeps the native bridge untouched and claim terminal."""
    store = requests_module.RequestStore(tmp_path / "requests.sqlite")
    record = capture(store)
    def failed_mark(*args):
        """Simulate unavailable persistence without echoing private diagnostic content."""
        raise RuntimeError("private disk detail")
    store.mark = failed_mark
    bridge = FakeBridge()
    audit = tmp_path / "audit.sqlite"
    args = decision(record)
    result = invoke(requests_module, store, audit, bridge, args)
    assert result == {"ok": False, "error": "request_store_unavailable", "outcome": "unknown"}
    assert invoke(requests_module, store, audit, bridge, args) == result
    assert not bridge.calls


def test_journal_finish_failure_does_not_retry(requests_module, tmp_path, monkeypatch):
    """Lost final receipts leave durable pending claims that prohibit native replay."""
    store = requests_module.RequestStore(tmp_path / "requests.sqlite")
    record = capture(store)
    original_finish = requests_module.Journal.finish
    def failed_finish(*args):
        """Simulate a crash after native acceptance and before final journal persistence."""
        raise RuntimeError("private disk detail")
    monkeypatch.setattr(requests_module.Journal, "finish", failed_finish)
    bridge = FakeBridge([{"status": "ok", "retcode": 0, "data": None}])
    audit = tmp_path / "audit.sqlite"
    args = decision(record)
    assert invoke(requests_module, store, audit, bridge, args)["error"] == "journal_finish_unknown"
    monkeypatch.setattr(requests_module.Journal, "finish", original_finish)
    assert invoke(requests_module, store, audit, bridge, args)["error"] == "operation_pending_or_unknown"
    assert len(bridge.calls) == 1


def test_missing_journal_prevents_native_submission(requests_module, tmp_path):
    """No implicit audit path or in-memory fallback can permit a remote mutation."""
    store = requests_module.RequestStore(tmp_path / "requests.sqlite")
    record = capture(store)
    bridge = FakeBridge()
    assert invoke(requests_module, store, None, bridge, decision(record))["error"] == "journal_unavailable"
    assert not bridge.calls and store.resolve(record["request_id"])["status"] == "pending"
