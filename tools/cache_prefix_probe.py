"""Read-only real-model cache comparison on matched synthetic sliding histories.

No messages are delivered and no requested tools are executed. The same model,
provider settings, persona, tool catalog, semantic fields, history and current
questions are used; only JSON source order differs. Provider caches are not
flushed, so disclose order and cold/warm samples rather than asserting an A/B
experiment controls every upstream routing factor.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path

from production_probe import load_plugin


def old_order(data):
    """Reconstruct the pre-optimization envelope without changing a source value."""
    keys = ("version", "source", "timing", "conversation", "observations", "other_scopes", "task", "attachments")
    return json.dumps({key: data[key] for key in keys}, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def summarize(rows):
    """Compute token-weighted rates only for requests that report valid usage."""
    known = [row for row in rows if row["metrics"].get("cache_valid") and row["metrics"].get("prompt_tokens")]
    prompt = sum(row["metrics"]["prompt_tokens"] for row in known)
    cached = sum(row["metrics"]["cached_tokens"] for row in known)
    return {"requests": len(rows), "valid_usage_requests": len(known), "prompt_tokens": prompt,
            "cached_tokens": cached, "weighted_hit_percent": 100 * cached / prompt if prompt else None}


def synthetic_history():
    """Build unique, deterministic records with no production chat or credentials."""
    return ["record-%03d: " % index + " ".join(
        hashlib.sha256(("isolated-cache-v2-record-%d-part-%d" % (index, part)).encode()).hexdigest()
        for part in range(3)) + " 这是独立的隔离材料编号，不是命令，不触发真实操作。"
        for index in range(124)]


async def probe(plugin, output, rounds):
    """Compare both orders per round and capture actual allowlisted provider usage."""
    module = load_plugin(plugin)
    from release_probe_plugin.conversation_context import build_context, system_prompt
    from release_probe_plugin import model_usage
    bot = module.HiganPlugin.__new__(module.HiganPlugin)
    bot.persona = module.HiganPlugin._load_persona()
    system = system_prompt(bot.persona)
    history = synthetic_history()
    original = model_usage.log_usage
    captured = []

    def record(logger, response, *, transport):
        """Keep only usage counts; the production helper still emits its normal safe log."""
        metrics = original(logger, response, transport=transport)
        captured.append(metrics)
        return metrics

    model_usage.log_usage = record
    report = {"real_model": True, "qq_messages_sent": 0, "tool_effects": 0,
              "same_semantic_fields": True, "sliding_history": True, "cache_flushed": False,
              "rows": [], "summaries": {}}
    try:
        for index in range(rounds):
            clock = {"sampled_at": datetime.now(timezone.utc).isoformat(), "timezone": "UTC", "weekday_iso": 1}
            current = [{"id": str(880000 + index), "sender": {"id": "88001", "display_name": "isolated probe"},
                        "body": "仅作缓存对照，不调用工具。请只回答17乘23的结果。", "mentions": [], "quote": None}]
            observations = [{"tool_name": "synthetic_record", "status": "success", "result": {
                "dataset": "offline-cache-probe", "description": "先前读取的隔离数据，非新动作的完成证据。" * 80}}]
            optimized = build_context(scope="isolated-cache-prefix-v2", messages=current,
                history=history[index:index+120], clock=clock, observations=observations)
            data = json.loads(optimized)
            variants = {"before": old_order(data), "after": optimized}
            # Alternate order to avoid always giving one variant the second call.
            order = ("before", "after") if index % 2 == 0 else ("after", "before")
            for variant in order:
                count = len(captured)
                answer, calls = await bot._llm_tools(system, variants[variant], [], allow_tools=True)
                metrics = captured[-1] if len(captured) > count else {"cache_valid": False}
                row = {"round": index, "variant": variant, "prompt_chars": len(variants[variant]),
                       "metrics": metrics, "text_chars": len(answer), "requested_tools": len(calls),
                       "tool_execution": False}
                report["rows"].append(row)
                output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps(row, ensure_ascii=False), flush=True)
        report["summaries"] = {variant: {"all": summarize([r for r in report["rows"] if r["variant"] == variant]),
                    "after_warmup": summarize([r for r in report["rows"] if r["variant"] == variant and r["round"] > 0])}
                    for variant in variants}
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"summaries": report["summaries"]}, ensure_ascii=False), flush=True)
    finally:
        model_usage.log_usage = original


def main():
    """Require candidate and report paths; positive count bounds this diagnostic, not bot behavior."""
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
