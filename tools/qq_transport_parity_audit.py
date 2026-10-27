"""Offline contract coverage gate; never authorizes production or proves native QQ.

Candidate JSON supplies action/event/semantic_requirements arrays of names only.
Matching them proves declared manifest coverage, not authenticity or live behavior.
No plugin imports, network requests, credentials, or production operations occur.
"""

import argparse
import json
from pathlib import Path
import re


DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "docs" / "qq-transport-required.json"
_NAME = re.compile(r"[A-Za-z_.][A-Za-z0-9_.]*\Z")


def _names(items, *, records=False):
    """Validate names strictly so duplicate/unknown-shaped claims fail closed."""
    if not isinstance(items, list):
        raise ValueError("capabilities_must_be_arrays")
    result = []
    for item in items:
        name = item.get("name") if records and isinstance(item, dict) else item
        if not isinstance(name, str) or not _NAME.fullmatch(name) or name in {".", ".."}:
            raise ValueError("invalid_capability_name")
        if name in result:
            raise ValueError("duplicate_capability_name")
        result.append(name)
    return set(result)


def _installed_names(installed, plugin_actions):
    """Validate static inventory structure and aliases without trusting runtime claims."""
    if (not isinstance(installed, dict)
            or type(installed.get("schema_version")) is not int
            or installed["schema_version"] != 1):
        raise ValueError("invalid_installed_inventory")
    completeness = installed.get("completeness")
    # A valid static inventory still never supplies native/runtime authorization.
    if (not isinstance(completeness, dict)
            or completeness.get("static_constructor_and_decorator_inventory") is not True
            or completeness.get("all_constructor_names_resolved_to_enum_literals") is not True):
        raise ValueError("incomplete_static_inventory")
    for key, value in completeness.items():
        if (key.endswith("_verified") or key.endswith("_complete")
                or key in {"static_constructor_and_decorator_inventory",
                           "all_constructor_names_resolved_to_enum_literals"}):
            if type(value) is not bool:
                raise ValueError("invalid_inventory_status")
    action_records = installed.get("actions")
    if not isinstance(action_records, list) or any(not isinstance(item, dict) for item in action_records):
        raise ValueError("installed_actions_must_be_records")
    base = _names(action_records, records=True)
    aliases = installed.get("generated_aliases")
    if not base or not isinstance(aliases, dict):
        raise ValueError("invalid_installed_actions")
    suffixes = aliases.get("suffixes")
    if (not isinstance(suffixes, list) or not suffixes
            or any(not isinstance(suffix, str) or not re.fullmatch(r"_[A-Za-z_]+", suffix)
                   for suffix in suffixes)
            or len(set(suffixes)) != len(suffixes)):
        raise ValueError("invalid_alias_suffixes")
    alias_names = _names(aliases.get("names"))
    expected = {name + suffix for name in base for suffix in suffixes}
    if alias_names != expected or base & alias_names:
        raise ValueError("invalid_generated_alias_names")
    if not plugin_actions <= base:
        raise ValueError("plugin_installed_inventory_drift")
    counts = installed.get("counts", {})
    if not isinstance(counts, dict):
        raise ValueError("invalid_inventory_counts")
    # Count metadata must be numeric integers, never bool (an int subclass).
    if any(type(value) is not int or value < 0 for value in counts.values()):
        raise ValueError("invalid_inventory_counts")
    expected_counts = {"unique_base_actions": len(base),
                       "unique_lookup_keys_with_aliases": len(base | alias_names)}
    if any(key in counts and counts[key] != value for key, value in expected_counts.items()):
        raise ValueError("inventory_count_mismatch")
    return base, alias_names


def evaluate(required, candidate=None, *, installed=None):
    """Compare declarations without mistaking any supplied evidence for verification.

    These booleans intentionally remain false even for complete/self-certified
    candidates: independent native implementation and live acceptance are separate.
    """
    if not isinstance(required, dict) or required.get("schema_version") != 1:
        raise ValueError("invalid_required_manifest")
    wanted = {key: _names(required.get(key), records=key != "semantic_requirements")
              for key in ("actions", "events", "semantic_requirements")}
    if any(not names for names in wanted.values()):
        raise ValueError("empty_required_contract")
    if candidate is not None and not isinstance(candidate, dict):
        raise ValueError("candidate_must_be_object")
    supplied = candidate or {}
    offered = {key: _names(supplied.get(key, [])) for key in wanted}
    missing = {key: sorted(wanted[key] - offered[key]) for key in wanted}
    extra = {key: sorted(offered[key] - wanted[key]) for key in wanted}
    installed_base, installed_aliases = (_installed_names(installed, wanted["actions"])
                                         if installed is not None else (set(), set()))
    missing_installed = sorted((installed_base | installed_aliases) - offered["actions"])
    installed_ready = installed is not None and candidate is not None and not missing_installed
    return {
        "mode": "offline_static_declaration_comparison",
        "source_commit": required.get("source_commit"),
        "required_counts": {key: len(value) for key, value in wanted.items()},
        "candidate_supplied": candidate is not None,
        "manifest_coverage_ready": (candidate is not None and not any(missing.values())
                                    and (installed is None or installed_ready)),
        "installed_inventory_supplied": installed is not None,
        "installed_required_counts": {"base": len(installed_base), "aliases": len(installed_aliases),
                                      "lookup": len(installed_base | installed_aliases)},
        "missing_installed_actions": missing_installed,
        "installed_declaration_coverage_ready": installed_ready,
        "missing": missing,
        "additional_declared": extra,
        "native_backend_implemented": False,
        "runtime_verified": False,
        "cutover_ready": False,
        "production_cutover_authorized": False,
        "blockers": required.get("cutover_blockers", []),
        "warning": "Coverage is a declaration only; never proves full QQ feature parity or native backend existence.",
    }


def main(argv=None):
    """Read explicit local JSON inputs and print a safe report with strict exit codes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--installed-inventory", type=Path)
    args = parser.parse_args(argv)
    try:
        required = json.loads(args.manifest.read_text(encoding="utf-8"))
        candidate = json.loads(args.candidate.read_text(encoding="utf-8")) if args.candidate else None
        installed = (json.loads(args.installed_inventory.read_text(encoding="utf-8"))
                     if args.installed_inventory else None)
        report = evaluate(required, candidate, installed=installed)
    except (OSError, ValueError, TypeError):
        # Do not echo file contents or raw exceptions, which could contain secrets.
        print(json.dumps({"error": "invalid_local_contract_input", "production_cutover_authorized": False}))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # Inventory without a candidate succeeds; partial candidates must fail checks.
    return 1 if args.candidate and not report["manifest_coverage_ready"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
