"""Exhaustive isolated advertised-tool contracts; no production side-effect claims."""

import asyncio
import copy
import importlib.util
import json
from pathlib import Path

import pytest

# Import the one audit implementation by explicit path; do not import plugin.main
# or rely on an unrelated installed package called tools/tests.
ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("higan_tool_contract_audit", ROOT / "tools" / "tool_contract_audit.py")
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    """Run actual production routes once, reverting all external-edge stubs afterward."""
    with pytest.MonkeyPatch.context() as patch:
        return AUDIT.audit(tmp_path_factory.mktemp("all-tool-contracts"), patch)


def test_every_advertised_name_executes_real_route(report):
    """Schema growth, unknown names, handler drift and route exceptions are failures."""
    assert not report["unmapped_cases"]
    assert not report["stale_cases"]
    assert not report["schema_errors"]
    assert not report["forbidden_io_attempts"]
    assert report["unknown_tool"]["rejected"]
    assert not report["gaps"], report["gaps"]
    assert report["route_contracts_pass"]
    assert len(report["tools"]) == report["effective_schema_count"]
    for tool in report["tools"]:
        assert tool["handler"] is not None, tool["name"]
        assert tool["handler_body_executed"], tool["name"]
        assert tool["executed_source_lines"], tool["name"]
        assert tool["result_quality"]["contract_satisfied"] is True, tool


def test_provider_all_calls_have_paired_ids_and_retained_observations(report):
    """Inject every name through actual provider transport/loop in bounded batches."""
    names = []
    for batch in report["provider_batches"]:
        assert 0 < batch["call_count"] <= 100
        assert batch["exception"] is None, batch
        assert batch["provider_requests"] == 2, batch
        assert batch["final_answer"] == "Audit complete.", batch
        final = json.loads(batch["final_raw"])
        assert final["delivery"] == "new" and final["text"] == batch["final_answer"], batch
        assert final["reply_to"], batch
        assert batch["exact_id_pairing"], batch
        assert batch["all_observations_retained"], batch
        assert batch["paired_results_match_archive"], batch
        assert batch["all_result_contracts_satisfied"], batch["failed_results"]
        names.extend(batch["names"])
    assert names == [tool["name"] for tool in report["tools"]]


def test_catalog_exposes_unique_neutral_tool_schemas(report):
    """The common catalog now resolves legacy declarations before runtime exposure."""
    assert report["duplicate_schema_names"] == {}
    assert report["raw_schemas_unique"] is True
    assert report["raw_schema_count"] == report["effective_schema_count"] == AUDIT.NATIVE_TOOL_COUNT
    names = [tool["name"] for tool in report["tools"]]
    assert len(names) == len(set(names))


def test_new_native_contracts_use_reviewed_receipts_and_actions(report):
    """The seven additions execute trusted native routes, not generic success stubs."""
    rows = {row["name"]: row for row in report["tools"]}
    for name in {*AUDIT.EXPECTED_SOCIAL_ACTIONS, "autonomy_status", "autonomy_schedule"}:
        assert rows[name]["handler_body_executed"], rows[name]
        assert rows[name]["boundary_contract_matches"], rows[name]
        assert rows[name]["result_quality"]["ok"] is True
    assert rows["qq_set_signature"]["result"]["verification"] == {"state": "matched", "matched": True}
    assert rows["qq_publish_post"]["result"]["outcome"] == "accepted"
    assert rows["qq_publish_post"]["result"]["verification"]["published"] is None
    assert rows["qq_social_status"]["result"]["runtime_verified"] is False
    assert rows["autonomy_schedule"]["result"]["settings"]["interval_seconds"] == 600


def test_request_contracts_distinguish_unavailable_from_route_errors(report):
    """All six native handlers execute while exactly three remain honestly unavailable."""
    rows = {row["name"]: row for row in report["tools"]}
    assert report["result_quality_counts"] == {"acknowledged": 103, "capability_unavailable": 3}
    for name in AUDIT.REQUEST_NAMES:
        assert rows[name]["handler_body_executed"] and rows[name]["boundary_contract_matches"], rows[name]
        assert rows[name]["result_quality"]["contract_satisfied"] is True
    for name in AUDIT.EXPECTED_UNAVAILABLE:
        row = rows[name]
        assert row["result"]["ok"] is False and row["result"]["supported"] is False
        assert row["result_quality"]["expected_unavailable"] is True
        assert row["stubbed_edges"] == [] and row["onebot_actions"] == []
    assert rows["qq_request_decide"]["result"]["outcome"] == "accepted"
    assert rows["qq_request_decide"]["result"]["membership"] == "unknown"
    assert sum(batch["expected_unavailable_count"] for batch in report["provider_batches"]) == 3


@pytest.mark.parametrize("name,result", [
    ("qq_send", {"ok": False, "error": "native_capability_unavailable", "supported": False,
                 "capability": "proactive_friend_add"}),
    ("qq_add_friend", {"ok": False, "error": "permission_denied"}),
    ("qq_add_friend", {"ok": False, "error": "native_capability_unavailable", "supported": True,
                      "capability": "proactive_friend_add"}),
    ("qq_join_group", {"ok": False, "error": "native_capability_unavailable", "supported": False,
                      "capability": "proactive_friend_add"}),
])
def test_expected_unavailable_quality_never_weakens_other_errors(name, result):
    """Mutation controls reject generic, unauthorized, or wrong-capability receipts."""
    assert AUDIT._quality(result, name)["contract_satisfied"] is False


def test_generic_success_stub_for_unavailable_request_action_is_a_route_gap(tmp_path, monkeypatch):
    """An unsupported native action cannot pass by returning an invented success object."""
    original = AUDIT.qq_requests.dispatch

    async def generic_success(name, *args, **kwargs):
        """Mutate one real native handler seam only, leaving all other routes intact."""
        if name == "qq_join_group":
            return {"ok": True, "joined": True}
        return await original(name, *args, **kwargs)

    monkeypatch.setattr(AUDIT.qq_requests, "dispatch", generic_success)
    with pytest.MonkeyPatch.context() as edges:
        mutated = AUDIT.audit(tmp_path, edges)
    row = next(row for row in mutated["tools"] if row["name"] == "qq_join_group")
    assert row["handler_body_executed"] is True
    assert row["boundary_contract_matches"] is False
    assert "qq_join_group" in {gap["name"] for gap in mutated["gaps"]}
    assert mutated["route_contracts_pass"] is False


def test_transfer_contracts_use_real_scoped_store_and_exact_native_boundaries(report):
    """The three transfer routes require actual source provenance and native receipts."""
    rows = {row["name"]: row for row in report["tools"]}
    for name in ("qq_forward_messages", "qq_copy_message", "qq_paste_message"):
        assert rows[name]["handler_body_executed"] and rows[name]["boundary_contract_matches"], rows[name]
        assert rows[name]["result_quality"]["contract_satisfied"] is True
    assert rows["qq_forward_messages"]["result"]["delivery_message_ids"] == ["71003"]
    assert rows["qq_paste_message"]["result"]["delivery_message_ids"] == ["71001"]
    assert rows["qq_copy_message"]["result"]["clipboard_id"]


@pytest.mark.parametrize("name", ["qq_forward_messages", "qq_copy_message", "qq_paste_message"])
def test_generic_success_cannot_mask_wrong_transfer_native_action(tmp_path, monkeypatch, name):
    """Each transfer audit gate rejects an invented success without its native IO."""
    original = AUDIT.message_transfer.dispatch

    async def generic_success(tool_name, *args, **kwargs):
        """Mutate only one native router seam while leaving schema and inventory real."""
        if tool_name == name:
            return {"ok": True, "delivered": True, "delivery_message_ids": ["invented"]}
        return await original(tool_name, *args, **kwargs)

    monkeypatch.setattr(AUDIT.message_transfer, "dispatch", generic_success)
    with pytest.MonkeyPatch.context() as edges:
        mutated = AUDIT.audit(tmp_path, edges)
    row = next(row for row in mutated["tools"] if row["name"] == name)
    assert row["handler_body_executed"] is True
    assert row["boundary_contract_matches"] is False
    assert name in {gap["name"] for gap in mutated["gaps"]}
    assert mutated["route_contracts_pass"] is False


def test_imported_workspace_discovery_and_workers_execute_real_contracts(report):
    """All additive classes require native receipts and locally exercised helpers."""
    rows = {row["name"]: row for row in report["tools"]}
    assert report["effective_schema_count"] == report["canonical_schema_count"] == 106
    assert report["workspace_helpers_executed"] >= 20
    assert report["native_worker_calls"]
    assert {row["status"] for row in report["native_worker_receipts"]} == {"completed", "cancelled"}
    assert all(row["result"] == "Offline worker complete." for row in report["native_worker_receipts"] if row["status"] == "completed")
    for builder in (AUDIT.workspace_tools.schema, AUDIT.tool_discovery.schema, AUDIT.delegation.schema, AUDIT.task_planning.schema):
        for schema in builder():
            row = rows[schema["function"]["name"]]
            assert row["boundary_contract_matches"], row
            assert row["handler_body_executed"], row
            assert row["result_quality"]["ok"] is True, row
    assert rows["vm_job_list"]["boundary_contract_matches"]
    assert rows["skill_save"]["result"]["name"] == "contract-save"


def test_reply_now_runs_native_send_with_only_event_transport_simulated(report):
    """The optional public reply uses real delivery policy and a recorded fixture edge."""
    row = next(row for row in report["tools"] if row["name"] == "reply_now")
    assert row["handler_body_executed"]
    assert row["actual_send_executed"]
    assert row["event_delivery_simulated"] is True
    assert row["boundary_contract_matches"], row
    assert row["sent_text"] == ["Contract interim reply."]
    assert row["result"] == {"ok": True, "delivered": True,
                             "text": "Contract interim reply.", "quote_id": None}
    assert not row["onebot_actions"]
    assert not row["stubbed_edges"]


def test_qq_send_runs_native_sender_and_requires_message_id_receipt(report):
    """Only OneBot transport is simulated; VM/generic-success sender stubs cannot pass."""
    row = next(row for row in report["tools"] if row["name"] == "qq_send")
    assert row["handler_body_executed"] and row["native_sender_executed"]
    assert row["delivery_transport_simulated"] is True
    assert row["boundary_contract_matches"], row
    assert row["result"] == {"ok": True, "delivered": True,
                             "message_id": "71001", "action": "send_group_msg"}
    assert row["stubbed_edges"] == [{"edge": "onebot_http", "action": "send_group_msg",
                                    "params": {"group_id": 42, "message": [
                                        {"type": "text", "data": {"text": "contract"}}]}}]


@pytest.mark.parametrize("arguments, action, destination, segments", [
    ({"kind": "group", "group_id": 42, "text": "literal [CQ:at,qq=777]", "mentions": ["10001"]},
     "send_group_msg", {"group_id": 42}, [
         {"type": "at", "data": {"qq": "10001"}},
         {"type": "text", "data": {"text": "literal [CQ:at,qq=777]"}}]),
    ({"kind": "private", "user_id": 10001, "text": "private contract"},
     "send_private_msg", {"user_id": 10001}, [
         {"type": "text", "data": {"text": "private contract"}}]),
    ({"kind": "temp", "user_id": 10001, "group_id": 42, "text": "temp contract"},
     "send_private_msg", {"user_id": 10001, "group_id": 42}, [
         {"type": "text", "data": {"text": "temp contract"}}]),
])
def test_native_qq_send_routes_targets_and_mentions_without_vm(tmp_path, monkeypatch, arguments,
                                                              action, destination, segments):
    """Real dispatch and sender preserve typed target IDs and never parse literal CQ text."""
    plugin = AUDIT.integration.production.__wrapped__(tmp_path, monkeypatch)()
    calls = []

    def onebot(native_action, params):
        """Record only the final native transport; all unapproved HTTP stays blocked."""
        calls.append((native_action, params))
        return {"status": "ok", "retcode": 0, "data": {"message_id": 71003}}

    plugin._onebot_http = onebot
    event = AUDIT.integration.Event("native typed contract")
    result = asyncio.run(plugin._dispatch_tool("qq_send", arguments, "42", event=event))
    assert calls == [(action, dict(destination, message=segments))]
    assert result == {"ok": True, "delivered": True, "message_id": "71003", "action": action}


def test_null_onebot_send_ack_is_not_a_successful_tool_contract(tmp_path, monkeypatch):
    """A legacy status=ok/data=null response must become an explicit audited gap."""
    original = AUDIT.AuditHarness.onebot

    def null_send(self, action, params):
        """Simulate only the known no-op acknowledgement without networking."""
        result = original(self, action, params)
        if action in {"send_group_msg", "send_private_msg"}:
            return {"status": "ok", "retcode": 0, "data": None}
        return result

    monkeypatch.setattr(AUDIT.AuditHarness, "onebot", null_send)
    with pytest.MonkeyPatch.context() as edges:
        result = AUDIT.audit(tmp_path, edges)
    row = next(row for row in result["tools"] if row["name"] == "qq_send")
    assert row["native_sender_executed"]
    assert row["result"]["ok"] is False and row["result"]["delivered"] is False
    assert row["result"]["error"] == "delivery_not_confirmed"
    assert not row["boundary_contract_matches"]
    assert "qq_send" in {gap["name"] for gap in result["gaps"]}
    assert result["route_contracts_pass"] is False
    assert result["forbidden_io_attempts"] == []


def test_recall_and_file_upload_run_native_actions_not_legacy_vm_success(report):
    """Recall keeps account ownership; file acknowledgements never invent message delivery."""
    rows = {row["name"]: row for row in report["tools"]}
    recall, upload = rows["vm_recall"], rows["send_file"]
    assert recall["boundary_contract_matches"] and recall["handler_body_executed"]
    assert [edge["action"] for edge in recall["stubbed_edges"]] == [
        "get_login_info", "get_group_msg_history", "delete_msg"]
    assert recall["result"]["results"] == [{"message_id": 81003, "ok": True, "status": "confirmed"}]
    assert upload["boundary_contract_matches"] and upload["handler_body_executed"]
    assert [edge["edge"] for edge in upload["stubbed_edges"]] == [
        "container_file_staging", "onebot_http"]
    assert upload["result"]["accepted"] is True and upload["result"]["delivered"] is False
    assert upload["result"]["delivery_confirmation"] == "native_upload_acknowledged"
    assert "message_id" not in upload["result"]


@pytest.mark.parametrize("target, action", [("vm_recall", "delete_msg"), ("send_file", "upload_group_file")])
def test_native_mutation_root_health_ack_cannot_pass_audit(tmp_path, monkeypatch, target, action):
    """A root-endpoint health response is not native recall or upload acceptance."""
    original = AUDIT.AuditHarness.onebot

    def health_ack(self, native_action, params):
        """Inject only the historical false-positive envelope, never a real request."""
        result = original(self, native_action, params)
        if native_action == action:
            return {"status": "ok", "retcode": 0, "data": None, "message": "NapCat is running"}
        return result

    monkeypatch.setattr(AUDIT.AuditHarness, "onebot", health_ack)
    with pytest.MonkeyPatch.context() as edges:
        report = AUDIT.audit(tmp_path, edges)
    row = next(row for row in report["tools"] if row["name"] == target)
    assert row["result"]["ok"] is False
    assert not row["boundary_contract_matches"]
    assert target in {gap["name"] for gap in report["gaps"]}
    assert report["route_contracts_pass"] is False
    assert report["forbidden_io_attempts"] == []


def test_history_search_uses_typed_persisted_source_and_excludes_foreign_private(report):
    """Native history retrieval retains source IDs and cannot search unrelated private stores."""
    row = next(row for row in report["tools"] if row["name"] == "history_search")
    assert row["boundary_contract_matches"] and row["handler_body_executed"]
    hits = row["result"]["hits"]
    assert hits and all(hit["scope"] == "42" for hit in hits)
    assert any(hit.get("record", {}).get("id") == "contract-history-42" for hit in hits)
    assert all(hit["source"]["trust"] == "untrusted_data" for hit in hits)
    assert "foreign private secret" not in str(hits)


def test_native_image_routes_parse_transport_html_without_sending(report):
    """Real image parsers expose candidate metadata, never unsolicited image sends."""
    rows = {row["name"]: row for row in report["tools"]}
    for name in ("image_search", "page_images"):
        row = rows[name]
        assert row["handler_body_executed"], row
        assert row["boundary_contract_matches"], row
        assert row["result_quality"]["ok"] is True, row
        assert [edge["edge"] for edge in row["stubbed_edges"]] == ["image_metadata_http"]
        assert not row["onebot_actions"]
        images = row["result"]["images"]
        assert len(images) == 1
        assert images[0]["verified_image_bytes"] is False
        assert images[0]["source_page"] == "https://example.org/article"
    assert rows["image_search"]["result"]["query"] == AUDIT.CASES["image_search"]["query"]
    assert rows["image_search"]["result"]["images"][0]["image_url"] == "https://cdn.example.org/contract-image.jpg"
    assert rows["page_images"]["result"]["images"][0]["image_url"] == "https://example.org/contract-page.jpg"


def test_schema_validator_detects_required_drift_and_type_errors():
    """Mutation controls prove the audit rejects malformed required contracts."""
    schema = {"type": "function", "function": {"name": "sample", "description": "test",
        "parameters": {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}}}
    assert AUDIT.schema_errors(schema) == []
    broken = copy.deepcopy(schema)
    broken["function"]["parameters"]["required"] = ["missing", "missing"]
    errors = AUDIT.schema_errors(broken)
    assert "parameters:required_not_in_properties" in errors
    assert "parameters:duplicate_required" in errors
    assert AUDIT.argument_errors(schema, {}) == ["missing:value"]
    assert AUDIT.argument_errors(schema, {"value": 1}) == ["wrong_type:value"]


def test_new_schema_without_reviewed_case_is_reported(tmp_path, monkeypatch):
    """An advertised name cannot pass solely because a generic stub says success."""
    original = AUDIT.integration._load_production

    def load_with_extra_schema(root):
        """Add an unknown advertised schema while leaving real dispatcher untouched."""
        klass = original(root)
        original_schemas = klass._tool_schemas

        def schemas(self):
            """Inject schema drift only, not an execution-handler replacement."""
            return original_schemas(self) + [{"type": "function", "function": {
                "name": "audit_new_unknown_tool", "description": "mutation control",
                "parameters": {"type": "object", "properties": {}, "required": []}}}]

        klass._tool_schemas = schemas
        return klass

    monkeypatch.setattr(AUDIT.integration, "_load_production", load_with_extra_schema)
    with pytest.MonkeyPatch.context() as edges:
        result = AUDIT.audit(tmp_path, edges)
    assert result["unmapped_cases"] == ["audit_new_unknown_tool"]
    assert not result["route_contracts_pass"]
    unknown = next(row for row in result["tools"] if row["name"] == "audit_new_unknown_tool")
    assert unknown["executed"] is False
    assert unknown["handler"] is None


def test_result_failure_and_handler_exception_are_not_hidden(tmp_path, monkeypatch):
    """A caught boundary error and an uncaught route error both create honest gaps."""
    original = AUDIT.AuditHarness.prepare

    def prepare_bad_coordinate(self, name):
        """Make one VM mapping fail before transport without replacing its router."""
        args, event = original(self, name)
        if name == "vm_click":
            args["x"] = "not-an-integer"
        return args, event

    def fail_fetch(self, url):
        """Raise at the already stubbed external edge, never attempt networking."""
        raise OSError("offline injected failure")

    monkeypatch.setattr(AUDIT.AuditHarness, "prepare", prepare_bad_coordinate)
    monkeypatch.setattr(AUDIT.AuditHarness, "fetch", fail_fetch)
    with pytest.MonkeyPatch.context() as edges:
        result = AUDIT.audit(tmp_path, edges)
    rows = {row["name"]: row for row in result["tools"]}
    assert rows["web_fetch"]["result_quality"]["ok"] is False
    assert rows["vm_click"]["exception"]["type"] == "ValueError"
    assert {"web_fetch", "vm_click"} <= {gap["name"] for gap in result["gaps"]}
    assert result["route_contracts_pass"] is False


def test_native_provider_honors_explicit_worker_schema_snapshot(tmp_path, monkeypatch):
    """Actual HTTP payloads isolate worker tools without mutating the parent catalog."""
    make_plugin = AUDIT.integration.production.__wrapped__(tmp_path, monkeypatch)
    plugin = make_plugin()
    parent = plugin._tool_schemas()
    explicit = [copy.deepcopy(next(item for item in parent if item["function"]["name"] == "web_search"))]
    snapshot = copy.deepcopy(explicit)
    requests = AUDIT.integration._provider(monkeypatch, [("child complete", []), ("parent complete", [])])

    async def exercise():
        """Use the production provider serializer for explicit and default catalogs."""
        await plugin._llm_tools("system", "prompt", [], messages=[], allow_tools=True, tool_schemas=explicit)
        await plugin._llm_tools("system", "prompt", [], messages=[], allow_tools=True)

    asyncio.run(exercise())
    assert requests[0]["tools"] == snapshot
    assert requests[1]["tools"] == parent
    assert len(requests[0]["tools"]) == 1
    assert len(requests[1]["tools"]) == 106
    assert explicit == snapshot
    assert plugin._tool_schemas() == parent


def test_audit_batch_limit_prevents_retention_overflow(tmp_path, monkeypatch):
    """Oversized batches must fail before fixture construction or tool execution."""
    with pytest.raises(ValueError, match="between 1 and 100"):
        AUDIT.audit(tmp_path, monkeypatch, 101)
