#!/usr/bin/env python3
"""Synthetic cache-prefix controls, offline by default and never reading live history.

Temporary JSONL archives exercise the real Recorder and CrossScopeMemory. Local
UTF-8 prefix lengths are exact byte comparisons, not provider cached-token claims.
The synthetic fixture is not evidence about a production model's context capacity.
Explicit --live makes four diagnostic requests, without executing returned tools.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import tempfile


def _load(path: Path, name: str):
    """Load a pure source module without importing the AstrBot plugin entrypoint."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Pure diagnostic module is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prefix(left: bytes, right: bytes) -> int:
    """Count exact shared UTF-8 bytes without interpreting them as model tokens."""
    for index, (a, b) in enumerate(zip(left, right)):
        if a != b:
            return index
    return min(len(left), len(right))


def _wire(records: list[dict], plugin: Path | str | None = None) -> bytes:
    """Reuse the actual pure context serializer with a frozen synthetic control tail."""
    root = Path(plugin) if plugin else Path(__file__).resolve().parents[1] / "plugin"
    context = _load(root / "conversation_context.py", "cache_probe_context_wire")
    return context.build_context(history=records, scope="101", messages=[],
                                 observations=[], clock={"synthetic_frozen_control": True}).encode("utf-8")


def build_cases(plugin: Path | str | None = None) -> dict:
    """Build real archived 500/501-row controls while retaining all native metadata."""
    root = Path(plugin) if plugin else Path(__file__).resolve().parents[1] / "plugin"
    history = _load(root / "history.py", "cache_probe_history")
    shared = _load(root / "cross_scope_memory.py", "cache_probe_cross_scope")
    with tempfile.TemporaryDirectory(prefix="qqbot-cache-synthetic-") as directory:
        recorder = history.Recorder(directory)
        def append(index: int) -> None:
            """Create explicitly synthetic IDs, Unicode text, quotes and nested metadata."""
            scope = str(101 + index % 3)
            body = "合成缓存对照，不是真实群聊。" + str(index) + "原始内容" * 8
            record = {"id": index, "sender": {"id": index % 7, "name": "synthetic"},
                      "message": body, "quote": {"id": index - 1, "text": "引号\\\"与换行\n均保留"},
                      "synthetic": True}
            # Direct fixture writes give deterministic native archive timestamps;
            # no production path or wall-clock metadata enters this diagnostic.
            row = {"ts": index, "line": body, "record": record}
            with (Path(directory) / (scope + ".jsonl")).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        for index in range(500):
            append(index)
        memory = shared.CrossScopeMemory(recorder)
        before = {"rolling": memory.unified_snapshot(limit=240, cache_block_size=0),
                  "aligned": memory.unified_snapshot(limit=240, cache_block_size=30)}
        append(500)
        after = {"rolling": memory.unified_snapshot(limit=240, cache_block_size=0),
                 "aligned": memory.unified_snapshot(limit=240, cache_block_size=30)}
        return {"before": before, "after": after}


def run_probe(plugin: Path | str | None = None) -> dict:
    """Return numeric, source-only controls and fail closed on lost recent records."""
    cases = build_cases(plugin)
    result = {"mode": "offline_synthetic", "provider_calls": 0, "production_history_reads": 0,
              "context_limit_declared": None, "provider_cache_effect": "unmeasured",
              "synthetic_archive_before": 500, "synthetic_archive_after": 501,
              "minimum_history": 240, "cache_block_size": 30, "cases": {}}
    for name in ("rolling", "aligned"):
        first = cases["before"][name]["records"]
        second = cases["after"][name]["records"]
        latest_before = cases["before"]["rolling"]["records"]
        latest_after = cases["after"]["rolling"]["records"]
        preserved = first[-240:] == latest_before and second[-240:] == latest_after
        intact = all(row.get("record", {}).get("synthetic") is True for row in first + second)
        chronological = all(a["ts"] <= b["ts"] for a, b in zip(second, second[1:]))
        padding = max(len(first) - 240, len(second) - 240)
        if not preserved or not intact or not chronological or padding > 29:
            raise AssertionError("Synthetic cache control violated the history preservation contract")
        a, b = _wire(first, plugin), _wire(second, plugin)
        result["cases"][name] = {"before_rows": len(first), "after_rows": len(second),
                                 "before_user_bytes": len(a), "after_user_bytes": len(b),
                                 "exact_shared_user_prefix_bytes": _prefix(a, b),
                                 "max_added_older_rows": padding, "latest_240_preserved": preserved,
                                 "native_metadata_preserved": intact, "chronological": chronological}
    result["prefix_gain_bytes"] = (result["cases"]["aligned"]["exact_shared_user_prefix_bytes"]
                                   - result["cases"]["rolling"]["exact_shared_user_prefix_bytes"])
    return result


def run_live_probe(plugin: Path | str) -> dict:
    """Opt in to four synthetic native-provider controls; never execute returned tools."""
    import os
    import time
    import urllib.request
    from production_probe import load_plugin
    from prompt_structure_cache_probe import native_usage, weighted
    directory = Path(plugin)
    report = run_probe(directory)
    cases = build_cases(directory)
    report.update(mode="live_synthetic", requests=[], cache_warmth_uncontrolled=True,
                  causal_improvement_asserted=False, qq_messages_sent=0,
                  tool_actions_executed=0, production_parameters_modified=False)
    with tempfile.TemporaryDirectory(prefix="shared-history-provider-control-") as temporary:
        previous = os.environ.get("HIGAN_ROOT")
        os.environ["HIGAN_ROOT"] = temporary
        try:
            module = load_plugin(directory)
            bot = module.HiganPlugin.__new__(module.HiganPlugin)
            base, key, model = bot._her_llm_config()
            if not base or not key or not model:
                raise RuntimeError("provider_not_configured")
            context = _load(directory / "conversation_context.py", "cache_probe_context")
            system = context.system_prompt(module.HiganPlugin._load_persona())
            tools = bot._tool_schemas()
            report["tool_schema_count"] = len(tools)
            # Native provider routing is resolved once and unchanged across controls.
            for layout in ("rolling", "aligned"):
                for step in ("before", "after"):
                    content = _wire(cases[step][layout]["records"], directory).decode("utf-8")
                    payload = {"model": model, "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": content}], "tools": tools,
                               "tool_choice": "auto", "max_tokens": 8}
                    request = urllib.request.Request(base + "/chat/completions",
                        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key}, method="POST")
                    started = time.monotonic()
                    report["provider_calls"] += 1
                    with urllib.request.urlopen(request, timeout=300) as response:
                        data = json.load(response)
                    calls = ((data.get("choices") or [{}])[0].get("message") or {}).get("tool_calls") or []
                    report["requests"].append({"layout": layout, "step": step, "usage": native_usage(data),
                        "seconds": round(time.monotonic() - started, 3), "tool_requests_not_executed": len(calls)})
        finally:
            if previous is None:
                os.environ.pop("HIGAN_ROOT", None)
            else:
                os.environ["HIGAN_ROOT"] = previous
    report["weighted_usage_by_layout"] = {name: weighted([row for row in report["requests"]
                                          if row["layout"] == name]) for name in ("rolling", "aligned")}
    return report


def main() -> None:
    """Print safe numeric diagnostics; live requests require explicit CLI opt-in."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin", type=Path, help="Pure local plugin source directory")
    parser.add_argument("--output", type=Path, help="Optional local numeric JSON destination")
    parser.add_argument("--live", action="store_true", help="Opt in to exactly four synthetic native provider requests")
    args = parser.parse_args()
    if args.live and not args.plugin:
        parser.error("--live requires an explicit --plugin source directory")
    result = run_live_probe(args.plugin) if args.live else run_probe(args.plugin)
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
