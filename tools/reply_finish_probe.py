"""Probe genuine model-selected reply_now finish with isolated state and no QQ sends."""

import argparse
import asyncio
from functools import wraps
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

from production_probe import ProbeEvent, load_plugin


async def probe(directory, output):
    """Exercise the production native completion callback without changing its logic."""
    # Set isolation before import, so lazily imported stores inherit the same root.
    with tempfile.TemporaryDirectory(prefix="reply_finish_probe_", dir=output.parent) as temporary:
        root = Path(temporary)
        previous_root = os.environ.get("HIGAN_ROOT")
        os.environ["HIGAN_ROOT"] = str(root)
        try:
            module = load_plugin(directory)
            from release_probe_plugin.message_batch import message_record
            from release_probe_plugin.tool_context import ToolContextArchive

            module.HIGAN_ROOT = str(root)
            bot = module.HiganPlugin.__new__(module.HiganPlugin)
            bot.persona = module.HiganPlugin._load_persona()
            bot.context = SimpleNamespace(get_using_provider=lambda *args: object())
            bot._pending = {}
            bot._busy = {}
            bot._all_recent = []
            bot._recent = {"release-probe": []}
            bot._recent_images = {}
            bot._member_snapshot = {}
            bot._history_origins = {}
            bot.manifest = []
            bot._provider_request_timeout = 300
            bot._tool_evidence_store = ToolContextArchive(root / "observations")
            bot._provider_requests = []
            published = []
            calls = []
            receipts = []
            finish_arguments = []
            terminal_request_counts = []
            rejected = []
            allowed = {"reply_now", "tool_describe"}

            async def record_send(event, scope, text, quote_id=None, *, decoded=False):
                """Record validated helper delivery locally; never address a QQ transport."""
                if not decoded:
                    raise RuntimeError("unvalidated_reply_payload")
                published.append(text)
                return text

            bot._send = record_send
            original_schemas = bot._tool_schemas

            def schemas():
                """Expose only the two reviewed tools, including pre-dispatch native paths."""
                return [schema for schema in original_schemas()
                        if schema.get("function", {}).get("name") in allowed]

            bot._tool_schemas = schemas
            original_native = bot._llm_tools
            original_fallback = bot._call_llm

            @wraps(original_native)
            async def native_request(*args, **kwargs):
                """Count genuine native requests and bound discovery without fabricating replies."""
                bot._provider_requests.append("native")
                # Tool discovery may need one round; the cap prevents runaway paid probes.
                if len(bot._provider_requests) > 3:
                    raise RuntimeError("probe_provider_request_limit")
                return await original_native(*args, **kwargs)

            @wraps(original_fallback)
            async def fallback_request(*args, **kwargs):
                """Count unexpected fallback completions while preserving the original provider."""
                bot._provider_requests.append("fallback")
                if len(bot._provider_requests) > 3:
                    raise RuntimeError("probe_provider_request_limit")
                return await original_fallback(*args, **kwargs)

            bot._llm_tools = native_request
            bot._call_llm = fallback_request
            original_dispatch = bot._dispatch_tool

            async def dispatch(name, args, scope="", event=None):
                """Delegate reviewed handlers only and retain metadata, never model text."""
                if name not in allowed:
                    rejected.append(name)
                    return {"ok": False, "error": "probe_readonly_boundary"}
                calls.append(name)
                if name == "reply_now":
                    finish_arguments.append(args.get("finish"))
                result = await original_dispatch(name, args, scope, event)
                if name == "reply_now":
                    # Only receipt keys and trusted booleans may enter the audit report.
                    receipts.append({"keys": sorted(result) if isinstance(result, dict) else [],
                                     "ok": isinstance(result, dict) and result.get("ok") is True,
                                     "delivered": isinstance(result, dict) and result.get("delivered") is True,
                                     "finish": isinstance(result, dict) and result.get("finish") is True})
                    if receipts[-1]["ok"] and receipts[-1]["delivered"] and receipts[-1]["finish"]:
                        terminal_request_counts.append(len(bot._provider_requests))
                return result

            bot._dispatch_tool = dispatch
            # This is an isolated user's request, not a production system/persona modification.
            text = ("隔离验收：请自行选择一句简短的最终回答内容，调用 reply_now 发送，显式设置 finish=true。"
                    "这一次就是完整答复，只调用一次 reply_now，无需额外总结。若需要确认参数，"
                    "只可先调用 tool_describe 查看 reply_now；不要调用其他工具、发送图片或创建文件。"
                    "这里只验证能力，不规定任何固定话术。")
            event = ProbeEvent(text)
            bot._turn_context = {"release-probe": {
                "messages": [message_record(event, text)], "history": (), "clock": bot._clock_context()}}
            final = await bot._decide_tools_first(event, "release-probe", text, False)
            finish_selected = len(finish_arguments) == 1 and finish_arguments[0] is True
            no_extra = (len(terminal_request_counts) == 1
                        and terminal_request_counts[0] == len(bot._provider_requests))
            # One provider call is ideal; a preceding tool_describe legitimately needs another.
            native_count = bot._provider_requests.count("native")
            expected_requests = 1 + calls.count("tool_describe")
            passed = (len(published) == 1 and bool(published[0]) and final == ""
                      and finish_selected and len(receipts) == 1 and receipts[0]["ok"]
                      and receipts[0]["delivered"] and receipts[0]["finish"] and no_extra
                      and native_count == expected_requests and 1 <= native_count <= 3
                      and "fallback" not in bot._provider_requests and not rejected)
            report = {
                "passed": passed,
                "tool_names": calls,
                "rejected_tool_count": len(rejected),
                "published": len(published),
                "published_text_chars": [len(value) for value in published],
                "final_text_chars": len(final),
                "final_empty": final == "",
                "finish_selected": finish_selected,
                "reply_finish_argument": finish_arguments[0] if finish_selected else None,
                "reply_finish_argument_is_bool": finish_selected,
                "native_helper_receipts": receipts,
                "provider_request_count": len(bot._provider_requests),
                "native_provider_request_count": native_count,
                "fallback_provider_request_count": bot._provider_requests.count("fallback"),
                "provider_request_count_at_finish": terminal_request_counts,
                "no_provider_request_after_finish": no_extra,
                "actual_model_provider": True,
                "actual_native_loop_dispatch_helper": True,
                "platform_send_replaced_with_local_recorder": True,
                "qq_messages_sent": 0,
                "state_isolated": True,
                "no_forced_runtime_reply_templates": True,
            }
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False), flush=True)
            return 0 if passed else 1
        finally:
            # Restore process environment even if loading, transport, or assertions fail.
            if previous_root is None:
                os.environ.pop("HIGAN_ROOT", None)
            else:
                os.environ["HIGAN_ROOT"] = previous_root


def main():
    """Require explicit plugin and metadata output paths; do not initiate deployment."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    return asyncio.run(probe(args.plugin.resolve(), args.output.resolve()))


if __name__ == "__main__":
    raise SystemExit(main())
