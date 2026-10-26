"""Compare timing-last sliding history with aligned history using real usage.

Run only on explicit operator invocation. No QQ delivery or requested tool runs
are possible through this one-call transport probe. Both variants use the same
provider configuration, persona, source dataset, scope, tool catalog, questions,
and fresh per-round clock. Aligned history retains additional older entries, so
inputs are not byte-identical and cache rates alone do not establish lower cost.
"""

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path

from cache_prefix_probe import summarize, synthetic_history
from production_probe import load_plugin


def totals(rows):
    """Extend shared cache aggregation with observed input, output and miss counts."""
    result = summarize(rows)
    prompt = [row["metrics"]["prompt_tokens"] for row in rows
              if row["metrics"].get("prompt_tokens") is not None]
    completion = [row["metrics"]["completion_tokens"] for row in rows
                  if row["metrics"].get("completion_tokens") is not None]
    # Shared summarize excludes missing/invalid cache telemetry. Do not label its
    # known-usage input sum as all input when only part of the calls reported it.
    valid = result["valid_usage_requests"]
    result.update({"reported_input_requests": len(prompt),
                   "total_reported_input_tokens": sum(prompt) if prompt else None,
                   "average_reported_input_tokens": sum(prompt) / len(prompt) if prompt else None,
                   "reported_output_requests": len(completion),
                   "total_reported_output_tokens": sum(completion) if completion else None,
                   "uncached_input_tokens": result["prompt_tokens"] - result["cached_tokens"] if valid else None})
    return result


def comparison(before, after):
    """Expose absolute usage deltas without assuming cached-token pricing."""
    fields = ("total_reported_input_tokens", "average_reported_input_tokens",
              "cached_tokens", "uncached_input_tokens", "total_reported_output_tokens",
              "weighted_hit_percent")
    return {"after_minus_before": {field: after[field] - before[field]
            if after[field] is not None and before[field] is not None else None for field in fields},
            "pricing_assumed": False,
            "cost_conclusion": "Compare both total input and uncached input; monetary savings require actual provider prices."}


async def probe(plugin, output, rounds):
    """Run matched single-call requests and persist allowlisted usage after each."""
    if rounds < 1:
        raise ValueError("rounds must be positive")
    module = load_plugin(plugin)
    from release_probe_plugin.conversation_context import build_context, system_prompt
    from release_probe_plugin.history_window import history_snapshot
    from release_probe_plugin import model_usage
    bot = module.HiganPlugin.__new__(module.HiganPlugin)
    bot.persona = module.HiganPlugin._load_persona()
    system = system_prompt(bot.persona)
    dataset = synthetic_history()
    # Reuse the deterministic existing dataset, extending only when an operator
    # explicitly requests more rounds; no fixed request count limits bot behavior.
    seed = tuple(dataset)
    while len(dataset) < 120 + rounds - 1:
        dataset.append(seed[len(dataset) % len(seed)] + " extension-%d" % len(dataset))
    original = model_usage.log_usage
    captured = []

    def record(logger, response, *, transport):
        """Capture normalized numeric fields without storing response or credentials."""
        metrics = original(logger, response, transport=transport)
        captured.append(metrics)
        return metrics

    report = {"real_model": True, "comparison": "timinglast_sliding_vs_timinglast_aligned",
              "scope": "isolated-history-v1", "same_provider_system_tools_dataset": True,
              "cache_flushed": False, "alternating_order": True,
              "qq_messages_sent": 0, "tool_effects": 0, "rows": [], "summaries": {},
              "limitations": ["Aligned input includes extra older source entries, disclosed per row.",
                              "Provider cache routing and cache lifetime are not controlled.",
                              "No monetary pricing assumption is made."]}
    model_usage.log_usage = record
    try:
        for index in range(rounds):
            now = datetime.now(timezone.utc)
            clock = {"sampled_at": now.isoformat(), "timezone": "UTC", "weekday_iso": now.isoweekday()}
            current = [{"id": str(880000 + index), "sender": {"id": "88001", "display_name": "isolated probe"},
                        "body": "仅作缓存对照，不调用工具。请只回答17乘23的结果。", "mentions": [], "quote": None}]
            history = dataset[:120 + index]
            variants = {"before": tuple(history[-120:]), "after": history_snapshot(history, 0)}
            order = ("before", "after") if index % 2 == 0 else ("after", "before")
            for variant in order:
                prompt = build_context(scope=report["scope"], messages=current, history=variants[variant], clock=clock)
                count = len(captured)
                answer, calls = await bot._llm_tools(system, prompt, [], allow_tools=True)
                metrics = captured[-1] if len(captured) > count else {"cache_valid": False}
                row = {"round": index, "variant": variant, "prompt_chars": len(prompt),
                       "history_entries": len(variants[variant]),
                       "extra_older_entries": len(variants[variant]) - min(120, len(history)),
                       "latest120_preserved": variants[variant][-120:] == tuple(history[-120:]),
                       "metrics": metrics, "text_chars": len(answer),
                       "answer_matches_391": str(answer).strip() == "391", "requested_tools": len(calls),
                       "tool_execution": False}
                report["rows"].append(row)
                output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps(row, ensure_ascii=False), flush=True)
        report["summaries"] = {variant: {
            "all": totals([row for row in report["rows"] if row["variant"] == variant]),
            "after_warmup": totals([row for row in report["rows"] if row["variant"] == variant and row["round"] > 0])}
            for variant in ("before", "after")}
        report["usage_comparison"] = {period: comparison(report["summaries"]["before"][period],
                                                        report["summaries"]["after"][period])
                                      for period in ("all", "after_warmup")}
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"summaries": report["summaries"], "usage_comparison": report["usage_comparison"]},
                         ensure_ascii=False), flush=True)
        return report
    finally:
        model_usage.log_usage = original


def main():
    """Require operator-chosen plugin/output paths and default to three probe rounds."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("rounds must be positive")
    asyncio.run(probe(args.plugin, args.output, args.rounds))


if __name__ == "__main__":
    main()
