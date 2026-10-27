"""Offline native forwarding contracts, scoped clipboard and terminal operation receipts."""

import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest


@pytest.fixture
def transfer(monkeypatch):
    """Import pure transfer helpers without activating the live plugin or its transport."""
    root = Path(__file__).resolve().parents[1] / "plugin"
    package = types.ModuleType("isolated_transfer_plugin")
    package.__path__ = [str(root)]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    for name in ("qq_delivery", "qq_native_actions", "qq_social", "message_transfer_store", "message_transfer"):
        spec = importlib.util.spec_from_file_location(package.__name__ + "." + name, root / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
    return module


def envelope(data=None, **changes):
    """Create a complete native protocol envelope rather than a transport-only success."""
    return {"status": "ok", "retcode": 0, "data": data, **changes}


def native_message(message_id=10, group_id=42, **changes):
    """Supply genuine-shaped metadata and rich segments whose fields must survive unchanged."""
    return {"message_id": message_id, "group_id": group_id, "message_type": "group", "time": 100 + message_id,
            "sender": {"user_id": 1234, "nickname": "原发送者"},
            "message": [{"type": "text", "data": {"text": "[CQ:at,qq=all] literal"}},
                        {"type": "image", "data": {"file": "native-image", "url": "https://private.example/image?token=secret"}},
                        {"type": "file", "data": {"file_id": "native-file", "name": "文件.bin"}}], **changes}


class FakeBridge:
    """Record exact native calls and yield fixed envelopes without network effects."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, action, params):
        """Preserve each payload and support simulated timeouts or cancellations."""
        self.calls.append((action, params))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def invoke(module, tmp_path, bridge, args, name="qq_forward_messages", scope="group:42", can_read_source=None, can_send_target=None):
    """Provide trusted parent context with durable temporary stores and explicit policies."""
    store_module = sys.modules[module.__package__ + ".message_transfer_store"]
    return asyncio.run(module.dispatch(name, args, bridge, scope=scope,
        store=store_module.TransferStore(tmp_path / "clipboard.sqlite3"), audit_path=tmp_path / "journal.sqlite3",
        can_read_source=can_read_source or allow, can_send_target=can_send_target or allow))


def allow(group_id):
    """Explicitly authorize valid positive group IDs in isolated tests only."""
    return type(group_id) is int and group_id > 0


def forward_args(**changes):
    """Return one stable explicit forwarding intent that tests can narrowly mutate."""
    return {"source_group_id": 42, "target_group_id": 99, "message_ids": [10, 11], "operation_id": "forward-1", **changes}


def test_schema_exposes_exact_tools_and_exclusive_selector(transfer):
    """The public schema requires explicit destination and exactly one selection method."""
    definitions = {item["function"]["name"]: item["function"]["parameters"] for item in transfer.schema()}
    assert set(definitions) == {"qq_forward_messages", "qq_copy_message", "qq_paste_message"}
    spec = definitions["qq_forward_messages"]
    assert spec["required"] == ["source_group_id", "target_group_id", "operation_id"]
    assert "maximum" not in spec["properties"]["count"]
    assert spec["properties"]["message_ids"]["minItems"] == 1
    assert spec["oneOf"] == [{"required": ["message_ids"], "not": {"required": ["count"]}}, {"required": ["count"], "not": {"required": ["message_ids"]}}]


@pytest.mark.parametrize("changes", [
    {"message_ids": []}, {"message_ids": [0]}, {"message_ids": [True]}, {"message_ids": ["-1"]},
    {"message_ids": ["１"]}, {"message_ids": [10, "010"]}, {"message_ids": ["0"]},
    {"message_ids": [10], "count": 1}, {"count": False}, {"mode": "fake"}, {"operation_id": " "},
    {"target_group_id": True}, {"source_group_id": -1}, {"sender": "invented"}, {"before_seq": 5},
])
def test_invalid_inputs_do_not_call_native(transfer, tmp_path, changes):
    """Reject ambiguous/coerced inputs before reading or making any native mutation."""
    bridge = FakeBridge()
    result = invoke(transfer, tmp_path, bridge, forward_args(**changes))
    assert result["error"] == "invalid_arguments"
    assert bridge.calls == []


@pytest.mark.parametrize("count", [None, 0, -1, True, 1.1, "1"])
def test_invalid_count_without_ids(transfer, tmp_path, count):
    """History count must be a positive real integer, never coerced or defaulted."""
    args = forward_args()
    args.pop("message_ids")
    if count is not None:
        args["count"] = count
    bridge = FakeBridge()
    assert invoke(transfer, tmp_path, bridge, args)["error"] == "invalid_arguments"
    assert not bridge.calls


def test_merged_native_reference_nodes_and_operation_replay(transfer, tmp_path):
    """All records are verified first; merged nodes contain real IDs and no fabricated author."""
    bridge = FakeBridge(envelope(native_message()), envelope(native_message(11)), envelope({"message_id": "900"}))
    args = forward_args(source_group_id="42", message_ids=["10", 11])
    result = invoke(transfer, tmp_path, bridge, args)
    assert result["ok"] and result["delivered"]
    assert result["delivery_message_ids"] == ["900"]
    assert bridge.calls == [("get_msg", {"message_id": 10}), ("get_msg", {"message_id": 11}),
                            ("send_group_forward_msg", {"group_id": 99, "messages": [{"type": "node", "data": {"id": "10"}}, {"type": "node", "data": {"id": "11"}}]})]
    assert invoke(transfer, tmp_path, bridge, args) == result
    assert len(bridge.calls) == 3
    assert "secret" not in json.dumps(result)
    # The journal stores only a digest and sanitized receipt, not native payload.
    assert b"private.example" not in (tmp_path / "journal.sqlite3").read_bytes()


def test_operation_id_conflicts_across_changed_intents(transfer, tmp_path):
    """Reusing an operation for a different target cannot trigger another send."""
    bridge = FakeBridge(envelope(native_message()), envelope(native_message(11)), envelope({"message_id": 900}))
    assert invoke(transfer, tmp_path, bridge, forward_args())["ok"]
    assert invoke(transfer, tmp_path, bridge, forward_args(target_group_id=100))["error"] == "operation_conflict"
    assert len(bridge.calls) == 3


def test_same_group_forward_is_allowed(transfer, tmp_path):
    """The source group may also be the authorized destination without a special bypass."""
    bridge = FakeBridge(envelope(native_message()), envelope({"message_id": 901}))
    result = invoke(transfer, tmp_path, bridge, forward_args(target_group_id=42, message_ids=[10]))
    assert result["delivered"] and bridge.calls[-1][1]["group_id"] == 42


def test_individual_native_forward_null_ack_is_not_delivery(transfer, tmp_path):
    """Installed single forwarding accepts null data; preserve acceptance without inventing IDs."""
    bridge = FakeBridge(envelope(native_message()), envelope(native_message(11)), envelope(), envelope())
    result = invoke(transfer, tmp_path, bridge, forward_args(mode="individual"))
    assert result["ok"] and result["accepted"] and not result["delivered"]
    assert result["outcome"] == "accepted" and result["accepted_count"] == 2
    assert result["delivery_message_ids"] == []
    assert bridge.calls[-2:] == [("forward_group_single_msg", {"group_id": "99", "message_id": 10}), ("forward_group_single_msg", {"group_id": "99", "message_id": 11})]
    assert invoke(transfer, tmp_path, bridge, forward_args(mode="individual")) == result


def test_history_count_passes_exact_native_request_without_cap(transfer, tmp_path):
    """Count is forwarded exactly; reverse native histories become chronological nodes."""
    args = forward_args(count=500, before_seq="800")
    args.pop("message_ids")
    bridge = FakeBridge(envelope({"messages": [native_message(11), native_message(10)]}), envelope({"message_id": 901}))
    result = invoke(transfer, tmp_path, bridge, args)
    assert result["selected_count"] == 2
    assert bridge.calls[0] == ("get_group_msg_history", {"group_id": "42", "count": 500, "reverseOrder": False, "message_seq": "800"})
    assert [node["data"]["id"] for node in bridge.calls[1][1]["messages"]] == ["10", "11"]


@pytest.mark.parametrize("record", [
    native_message(group_id=999), native_message(message_type="private"), native_message(message_type="temp"), native_message(message_type=None),
    native_message(message_id=999), native_message(group_id=None), native_message(message="[CQ:image,file=a]"),
    native_message(message=[]), native_message(message=[{"type": "text", "data": "bad"}]),
    native_message(message=[{"type": "text", "data": {"score": float("nan")}}]),
])
def test_all_sources_verified_before_any_send(transfer, tmp_path, record):
    """A later foreign/private/malformed record prevents the entire selected batch send."""
    bridge = FakeBridge(envelope(native_message(11)), envelope(record))
    result = invoke(transfer, tmp_path, bridge, forward_args(message_ids=[11, 10]))
    assert result["error"] == "source_lookup_failed" and not result["delivered"]
    assert [action for action, _ in bridge.calls] == ["get_msg", "get_msg"]


@pytest.mark.parametrize("response", [envelope(), {"message_id": 900}, envelope({"message_id": 900}, retcode=False),
                                       envelope({"message_id": 900}, status="async"), envelope({"message_id": 900}, message="NapCat4 Is Running")])
def test_lookup_requires_actual_envelope_and_group_provenance(transfer, tmp_path, response):
    """Health responses, missing data and unverified provenance cannot select source records."""
    bridge = FakeBridge(response)
    assert invoke(transfer, tmp_path, bridge, forward_args(message_ids=[10]))["error"] == "source_lookup_failed"
    assert len(bridge.calls) == 1


@pytest.mark.parametrize("read,send,error", [(False, True, "source_permission_denied"), (True, False, "target_permission_denied"), (True, 1, "target_permission_denied")])
def test_denied_policy_precedes_all_native_calls(transfer, tmp_path, read, send, error):
    """Only an explicit boolean trusted authorization permits any source read or send."""
    def read_policy(group_id):
        """Return the simulated source visibility result."""
        return read
    def send_policy(group_id):
        """Return the simulated target policy result."""
        return send
    bridge = FakeBridge()
    assert invoke(transfer, tmp_path, bridge, forward_args(), can_read_source=read_policy, can_send_target=send_policy)["error"] == error
    assert not bridge.calls


def test_individual_policy_rechecked_before_each_send(transfer, tmp_path):
    """A policy change after one accepted forward stops the batch with an honest partial receipt."""
    checks = []
    def changing_policy(group_id):
        """Authorize initial preflight and first send but deny the following send."""
        checks.append(group_id)
        return len(checks) < 3
    bridge = FakeBridge(envelope(native_message()), envelope(native_message(11)), envelope())
    args = forward_args(mode="individual")
    result = invoke(transfer, tmp_path, bridge, args, can_send_target=changing_policy)
    assert result["outcome"] == "partial" and result["accepted_count"] == 1
    assert result["error"] == "target_permission_denied"
    assert len(bridge.calls) == 3 and checks == [99, 99, 99]
    assert invoke(transfer, tmp_path, bridge, args) == result


@pytest.mark.parametrize("failure", [TimeoutError("secret transport credential"), asyncio.CancelledError(), envelope(None, status="failed", retcode=100), {"status": "ok"}])
def test_individual_failure_stops_and_never_retries(transfer, tmp_path, failure):
    """Known or unknown second-item failure cannot repeat the successful first send."""
    bridge = FakeBridge(envelope(native_message()), envelope(native_message(11)), envelope(), failure)
    args = forward_args(mode="individual")
    result = invoke(transfer, tmp_path, bridge, args)
    assert not result["ok"] and not result["delivered"] and result["accepted_count"] == 1
    assert result["outcome"] in ("unknown", "partial")
    assert "secret" not in json.dumps(result)
    assert invoke(transfer, tmp_path, bridge, args) == result
    assert len(bridge.calls) == 4


@pytest.mark.parametrize("response", [envelope(), envelope({"message_id": 0}), envelope({"message_id": True}), {"status": "ok"}, TimeoutError("secret")])
def test_merged_unknown_ack_has_no_retry(transfer, tmp_path, response):
    """Merged sends require a positive actual native output ID, including on replay."""
    bridge = FakeBridge(envelope(native_message()), response)
    args = forward_args(message_ids=[10])
    result = invoke(transfer, tmp_path, bridge, args)
    assert not result["ok"] and not result["delivered"] and result["outcome"] == "unknown"
    assert invoke(transfer, tmp_path, bridge, args) == result
    assert len(bridge.calls) == 2


def test_copy_then_paste_exact_native_segments_as_bot(transfer, tmp_path):
    """Copy reads only; later paste emits the original rich array without author impersonation."""
    record = native_message()
    bridge = FakeBridge(envelope(record), envelope({"message_id": 902}))
    copied = invoke(transfer, tmp_path, bridge, {"source_group_id": 42, "message_id": 10}, name="qq_copy_message")
    assert copied["ok"] and len(bridge.calls) == 1
    assert set(copied) == {"ok", "clipboard_id", "source_group_id", "source_message_id"}
    pasted = invoke(transfer, tmp_path, bridge, {"clipboard_id": copied["clipboard_id"], "target_group_id": 99, "operation_id": "paste-1"}, name="qq_paste_message")
    assert pasted["delivered"]
    assert bridge.calls[1] == ("send_group_msg", {"group_id": 99, "message": record["message"]})
    assert "secret" not in json.dumps(copied) + json.dumps(pasted)


def test_clipboard_scope_isolation_and_paste_source_recheck(transfer, tmp_path):
    """Knowledge of a clipboard ID cannot expose another conversation or revoked source."""
    bridge = FakeBridge(envelope(native_message()))
    copied = invoke(transfer, tmp_path, bridge, {"source_group_id": 42, "message_id": 10}, name="qq_copy_message")
    args = {"clipboard_id": copied["clipboard_id"], "target_group_id": 99, "operation_id": "paste-1"}
    other = invoke(transfer, tmp_path, bridge, args, name="qq_paste_message", scope="group:99")
    assert other["error"] == "clipboard_not_found"
    def denied(group_id):
        """Simulate revoked source visibility after a previously authorized copy."""
        return False
    revoked = invoke(transfer, tmp_path, bridge, args, name="qq_paste_message", can_read_source=denied)
    assert revoked["error"] == "source_permission_denied"
    assert len(bridge.calls) == 1


def test_copy_is_repeatable_metadata_only(transfer, tmp_path):
    """Identical native copy snapshots produce the same opaque ID without sending anything."""
    bridge = FakeBridge(envelope(native_message()), envelope(native_message()))
    args = {"source_group_id": "42", "message_id": "10"}
    first = invoke(transfer, tmp_path, bridge, args, name="qq_copy_message")
    second = invoke(transfer, tmp_path, bridge, args, name="qq_copy_message")
    assert first == second and len(first["clipboard_id"]) == 32
    assert [action for action, _ in bridge.calls] == ["get_msg", "get_msg"]


@pytest.mark.parametrize("times", [(200, 200), (None, 100)])
def test_history_equal_or_absent_timestamps_retain_native_order(transfer, tmp_path, times):
    """Stable ties and unknown epochs must not fabricate chronology from native IDs."""
    args = forward_args(count=2)
    args.pop("message_ids")
    first, second = native_message(11), native_message(10)
    if times[0] is None:
        first.pop("time")
    else:
        first["time"] = times[0]
    second["time"] = times[1]
    bridge = FakeBridge(envelope({"messages": [first, second]}), envelope({"message_id": 900}))
    assert invoke(transfer, tmp_path, bridge, args)["delivered"]
    assert [node["data"]["id"] for node in bridge.calls[1][1]["messages"]] == ["11", "10"]


def test_absent_message_type_is_not_assumed_group(transfer, tmp_path):
    """A missing native type cannot be replaced with a guessed group provenance."""
    record = native_message()
    record.pop("message_type")
    bridge = FakeBridge(envelope(record))
    assert invoke(transfer, tmp_path, bridge, forward_args(message_ids=[10]))["error"] == "source_lookup_failed"
    assert len(bridge.calls) == 1


def test_health_greeting_cannot_confirm_send(transfer, tmp_path):
    """Even an ID-shaped root health reply cannot prove the requested native mutation."""
    bridge = FakeBridge(envelope(native_message()), envelope({"message_id": 900}, message="NapCat4 Is Running"))
    result = invoke(transfer, tmp_path, bridge, forward_args(message_ids=[10]))
    assert not result["delivered"] and result["outcome"] == "unknown"
    assert len(bridge.calls) == 2


def test_interrupted_claim_never_attempts_native_lookup(transfer, tmp_path):
    """A process death after claim remains terminal rather than triggering hidden resend."""
    args = transfer._validate("qq_forward_messages", forward_args())
    operation = "qq_message_transfer:" + json.dumps(["group:42", args["operation_id"]], ensure_ascii=True, separators=(",", ":"))
    digest = transfer.hashlib.sha256(json.dumps(["qq_forward_messages", args], sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    transfer.Journal(tmp_path / "journal.sqlite3").claim(operation, digest)
    bridge = FakeBridge()
    result = invoke(transfer, tmp_path, bridge, args)
    assert result["error"] == "operation_pending_or_unknown" and result["outcome"] == "unknown"
    assert bridge.calls == []
