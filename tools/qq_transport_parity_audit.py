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
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.]*\Z")


def _names(items, *, records=False):
    """Validate names strictly so duplicate/unknown-shaped claims fail closed."""
    if not isinstance(items, list):
        raise ValueError("capabilities_must_be_arrays")
    result = []
    for item in items:
        name = item.get("name") if records and isinstance(item, dict) else item
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError("invalid_capability_name")
        if name in result:
            raise ValueError("duplicate_capability_name")
        result.append(name)
    return set(result)


def evaluate(required, candidate=None):
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
    return {
        "mode": "offline_static_declaration_comparison",
        "source_commit": required.get("source_commit"),
        "required_counts": {key: len(value) for key, value in wanted.items()},
        "candidate_supplied": candidate is not None,
        "manifest_coverage_ready": candidate is not None and not any(missing.values()),
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
    args = parser.parse_args(argv)
    try:
        required = json.loads(args.manifest.read_text(encoding="utf-8"))
        candidate = json.loads(args.candidate.read_text(encoding="utf-8")) if args.candidate else None
        report = evaluate(required, candidate)
    except (OSError, ValueError, TypeError):
        # Do not echo file contents or raw exceptions, which could contain secrets.
        print(json.dumps({"error": "invalid_local_contract_input", "production_cutover_authorized": False}))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # Inventory without a candidate succeeds; partial candidates must fail checks.
    return 1 if args.candidate and not report["manifest_coverage_ready"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
