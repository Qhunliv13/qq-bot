"""Exhaustive isolated advertised-tool contracts; no production side-effect claims."""

import asyncio
import copy
import importlib.util
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
        assert tool["result_quality"]["ok"] is True, tool


def test_provider_all_calls_have_paired_ids_and_retained_observations(report):
    """Inject every name through actual provider transport/loop in bounded batches."""
    names = []
    for batch in report["provider_batches"]:
        assert 0 < batch["call_count"] <= 100
        assert batch["exception"] is None, batch
        assert batch["provider_requests"] == 2, batch
        assert batch["final_answer"] == "Audit complete.", batch
        assert batch["exact_id_pairing"], batch
        assert batch["all_observations_retained"], batch
        assert batch["paired_results_match_archive"], batch
        assert batch["all_results_successful"], batch["failed_results"]
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


def test_imported_workspace_discovery_and_workers_execute_real_contracts(report):
    """All additive classes require native receipts and locally exercised helpers."""
    rows = {row["name"]: row for row in report["tools"]}
    assert report["effective_schema_count"] == report["canonical_schema_count"] == 92
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
    assert len(requests[1]["tools"]) == 92
    assert explicit == snapshot
    assert plugin._tool_schemas() == parent


def test_audit_batch_limit_prevents_retention_overflow(tmp_path, monkeypatch):
    """Oversized batches must fail before fixture construction or tool execution."""
    with pytest.raises(ValueError, match="between 1 and 100"):
        AUDIT.audit(tmp_path, monkeypatch, 101)
