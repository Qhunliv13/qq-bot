#!/usr/bin/env python3
"""Offline chat-retention measurements; no model, account or private state access."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugin.context_budget import allocation, size, suffix  # noqa: E402


def measure(maximum=600000, fixed=50000):
    """Compare prior 1/8 reservation with the actual 60/40 conversation regions.

    Fixture strings contain no real conversations. Omission measures raw model
    context only; archives are neither opened nor changed by this pure probe.
    """
    regions = allocation(maximum, fixed)
    old_raw = max(0, maximum - fixed - maximum // 8 - 2048)
    cases = []
    for count, width in ((100, 50), (1000, 500), (100, 10000), (1, maximum + 1)):
        rows = [{"scope": "synthetic", "record": {"id": str(index), "body": "x" * width}}
                for index in range(count)]
        kept, coverage = suffix(rows, regions["raw_chars"])
        previous, old_coverage = suffix(rows, old_raw)
        cases.append({"fixture": {"records": count, "body_chars_each": width},
                      "source_record_chars": sum(size(row) for row in rows),
                      "previous_raw_records": len(previous),
                      "previous_omitted_record_chars": old_coverage["omitted_source_chars"],
                      "raw_records": len(kept), "raw_record_chars": coverage["retained_source_chars"],
                      "raw_source_fraction": coverage["retained_source_fraction"],
                      "to_summary_or_archive_records": coverage["omitted"],
                      "to_summary_or_archive_chars": coverage["omitted_source_chars"],
                      "automatic_archive_deletions": 0})
    return {"mode": "synthetic_offline_no_private_data", "maximum_chars": maximum,
            "fixed_chars": fixed, "allocation": regions, "cases": cases,
            "limitations": ["Character length is not token count.",
                "60/40 splits available context, not the entire growing archive.",
                "Whole-record boundaries can leave raw space unused.",
                "No archive deletion is not a guarantee of disk-write success.",
                "Compression is lossy; archive-only evidence requires explicit retrieval."]}


def main():
    """Print deterministic numeric evidence without reading private deployment settings."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-chars", type=int, default=600000)
    parser.add_argument("--fixed-chars", type=int, default=50000)
    args = parser.parse_args()
    print(json.dumps(measure(args.max_chars, args.fixed_chars), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
