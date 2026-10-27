"""Pure local tests: declarations never become native QQ or production proof."""

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("qq_transport_parity_audit", ROOT / "tools" / "qq_transport_parity_audit.py")
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


@pytest.fixture
def required():
    """Read only the public non-secret static contract manifest."""
    return json.loads(AUDIT.DEFAULT_MANIFEST.read_text(encoding="utf-8"))


def complete_candidate(required):
    """Generate test declarations, deliberately NOT real runtime evidence."""
    return {key: [item["name"] for item in required[key]] for key in ("actions", "events")} | {
        "semantic_requirements": list(required["semantic_requirements"])}


def test_default_reports_pending(required):
    """An inventory with no candidate must advertise every missing capability."""
    result = AUDIT.evaluate(required)
    assert result["required_counts"]["actions"] == 21
    assert result["required_counts"]["events"] == 7
    assert not result["manifest_coverage_ready"]
    assert not result["cutover_ready"]
    assert not result["native_backend_implemented"]
    assert not result["production_cutover_authorized"]
    assert required["coverage_scope"] == "observed_current_bot_only"
    assert required["full_installed_backend_inventory_complete"] is False


def test_complete_claims_do_not_authorize_cutover(required):
    """Even fabricated full-support evidence cannot open the production gate."""
    candidate = complete_candidate(required)
    candidate.update(native_backend_implemented=True, runtime_verified=True,
                     cutover_ready=True, evidence={"status": "verified"})
    result = AUDIT.evaluate(required, candidate)
    assert result["manifest_coverage_ready"]
    assert not any(result["missing"].values())
    assert not result["runtime_verified"]
    assert not result["native_backend_implemented"]
    assert not result["production_cutover_authorized"]
    assert not result["cutover_ready"]


@pytest.mark.parametrize("key", ["actions", "events", "semantic_requirements"])
def test_missing_contract_category_fails(required, key):
    """Dropping any existing semantic or event dependency blocks declared parity."""
    candidate = complete_candidate(required)
    candidate[key].pop()
    assert not AUDIT.evaluate(required, candidate)["manifest_coverage_ready"]


@pytest.mark.parametrize("bad", [None, "send_group_msg", ["bad/action"], ["x", "x"], [True], [{"name": "send_group_msg"}]])
def test_invalid_candidate_entries_rejected(required, bad):
    """Malformed or duplicate claims cannot be silently normalized into support."""
    with pytest.raises(ValueError):
        AUDIT.evaluate(required, {"actions": bad})


def test_partial_candidate_cli_fails(required, tmp_path, capsys):
    """A partial comparison exits nonzero without making any live request."""
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps({"actions": []}), encoding="utf-8")
    assert AUDIT.main(["--candidate", str(candidate)]) == 1
    assert json.loads(capsys.readouterr().out)["production_cutover_authorized"] is False


def test_malformed_cli_safe(tmp_path, capsys):
    """Invalid local JSON returns a safe diagnostic without echoing input."""
    candidate = tmp_path / "candidate.json"
    candidate.write_text("SECRET_NON_JSON", encoding="utf-8")
    assert AUDIT.main(["--candidate", str(candidate)]) == 2
    output = capsys.readouterr().out
    assert "SECRET" not in output
    assert json.loads(output)["production_cutover_authorized"] is False


@pytest.fixture
def installed():
    """Read the sanitized installed static inventory, never software implementation."""
    return json.loads((ROOT / "docs" / "qq-installed-action-inventory.json").read_text(encoding="utf-8"))


def test_installed_inventory_counts_and_pending(required, installed):
    """The explicit inventory expands declaration requirements, not runtime proof."""
    result = AUDIT.evaluate(required, installed=installed)
    assert result["installed_inventory_supplied"]
    assert result["installed_required_counts"] == {"base": 180, "aliases": 360, "lookup": 540}
    assert len(result["missing_installed_actions"]) == 540
    assert not result["installed_declaration_coverage_ready"]


def test_plugin_only_fails_installed_coverage(required, installed):
    """The old 21-action candidate cannot masquerade as full installed coverage."""
    result = AUDIT.evaluate(required, complete_candidate(required), installed=installed)
    assert not any(result["missing"].values())
    assert len(result["missing_installed_actions"]) == 519
    assert not result["manifest_coverage_ready"]


def test_all_installed_claims_still_never_authorize(required, installed):
    """Even 540 declarations and fabricated proof claims cannot authorize cutover."""
    candidate = complete_candidate(required)
    candidate["actions"] = [r["name"] for r in installed["actions"]] + installed["generated_aliases"]["names"]
    candidate.update(runtime_verified=True, cutover_ready=True, native_backend_implemented=True)
    result = AUDIT.evaluate(required, candidate, installed=installed)
    assert result["manifest_coverage_ready"] and result["installed_declaration_coverage_ready"]
    assert result["missing_installed_actions"] == []
    assert not result["runtime_verified"] and not result["native_backend_implemented"]
    assert not result["cutover_ready"] and not result["production_cutover_authorized"]


def test_installed_duplicate_base_rejected(required, installed):
    """Duplicate inventory rows fail instead of silently shrinking the denominator."""
    installed["actions"].append(installed["actions"][0])
    with pytest.raises(ValueError):
        AUDIT.evaluate(required, installed=installed)


def test_installed_bad_alias_rejected(required, installed):
    """A plausible but non-derived alias must not count as installed evidence."""
    installed["generated_aliases"]["names"][0] += "_fake"
    with pytest.raises(ValueError):
        AUDIT.evaluate(required, installed=installed)


def test_installed_count_boolean_rejected(required, installed):
    """Python bool being an int subtype must not validate count metadata."""
    installed["counts"]["unique_base_actions"] = True
    with pytest.raises(ValueError):
        AUDIT.evaluate(required, installed=installed)


def test_installed_plugin_drift_rejected(required, installed):
    """An inventory from a mismatched backend cannot satisfy the plugin baseline."""
    required["actions"].append({"name": "not_installed_action"})
    with pytest.raises(ValueError):
        AUDIT.evaluate(required, installed=installed)


def test_installed_cli_and_prefix_names(required, tmp_path, capsys):
    """CLI accepts explicit installed input while real dotted/underscore names work."""
    assert AUDIT._names([".ocr_image", "_mark_all_as_read"]) == {".ocr_image", "_mark_all_as_read"}
    for name in [".", "..", "bad/action"]:
        with pytest.raises(ValueError):
            AUDIT._names([name])
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps(complete_candidate(required)), encoding="utf-8")
    inventory_path = ROOT / "docs" / "qq-installed-action-inventory.json"
    assert AUDIT.main(["--candidate", str(candidate), "--installed-inventory", str(inventory_path)]) == 1
    assert json.loads(capsys.readouterr().out)["installed_inventory_supplied"]


def test_all_source_sites_exist(required):
    """Keep every reported source reference within an existing local source file."""
    for key in ("actions", "events", "transport_dependencies"):
        for record in required[key]:
            for site in record["sites"]:
                filename, line = site.rsplit(":", 1)
                source = ROOT / filename
                assert source.is_file()
                assert 0 < int(line) <= len(source.read_text(encoding="utf-8").splitlines())
