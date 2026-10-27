"""Isolated real-model conditional-history probe; never posts or mutates live state."""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import tempfile
import types
import uuid

from production_probe import ProbeEvent, load_plugin


def _record(identity, body, role, message_id):
    """Keep synthetic sender evidence explicit without borrowing real participant IDs."""
    return {"id": message_id, "sender": {"id": identity, "display_name": identity, "role": role},
            "body": body, "native": {"sender": {"user_id": identity}},
            "time": "2001-01-01T00:00:00+00:00"}


async def _no_refs(*args):
    """Forbid attachment lookup while preserving native model transport and parameters."""
    return [], ""


async def probe(plugin, output):
    """Exercise three small fixtures through the native loop with every tool guarded."""
    nonce = uuid.uuid4().hex
    source, result = "source_marker_" + nonce, "result_marker_" + nonce
    condition = "Only when new incoming text is `" + source + "` reply `" + result + "`."
    report = {"real_model": True, "fixture_only": True, "live_message_delivery_tested": False,
              "qq_messages_sent": 0, "all_tool_effects_forbidden": True,
              "input_fixture_sha256": hashlib.sha256(condition.encode()).hexdigest(),
              "scenarios": []}
    # Root is set before import: import-time consumers cannot bind live durable stores.
    previous_root = os.environ.get("HIGAN_ROOT")
    with tempfile.TemporaryDirectory(prefix="instruction-provenance-probe-") as temporary:
        os.environ["HIGAN_ROOT"] = temporary
        try:
            module = load_plugin(plugin)
            from release_probe_plugin.history import Recorder
            from release_probe_plugin.message_batch import message_record
            from release_probe_plugin.reply_selection import decode_reply
            from release_probe_plugin.tool_context import ToolContextArchive
            cases = [("condition_lacking", "unrelated_current_" + nonce, False),
                     ("historical_match_only", "another_current_" + nonce, True),
                     ("current_match_positive", source, False)]
            for label, current, old_match in cases:
                scope = "isolated-instruction-provenance"
                bot = module.HiganPlugin.__new__(module.HiganPlugin)
                bot.persona = "You are a conversational assistant."
                bot.context = types.SimpleNamespace(get_using_provider=lambda *args: object())
                bot._pending, bot._busy, bot._all_recent = {}, {}, []
                bot._recent, bot._recent_images, bot._member_snapshot, bot.manifest = {scope: []}, {}, {}, []
                case_root = Path(temporary) / label
                bot._tool_evidence_store = ToolContextArchive(case_root / "observations")
                recorder = Recorder(str(case_root / "history"))
                bot._hist = lambda: recorder
                history = [{"scope": scope, "origin_scope": scope,
                            "line": condition,
                            "record": _record("fixture-owner", condition, "owner", "old-condition")}]
                # A previous matching event and completed response stay historical data.
                if old_match:
                    history.extend([
                        {"scope": scope, "origin_scope": scope, "line": source,
                         "record": _record("fixture-member", source, "member", "old-source")},
                        {"scope": scope, "origin_scope": scope, "line": result,
                         "record": _record("fixture-assistant", result, "assistant", "old-result")}])
                event = ProbeEvent(current)
                event.message_obj.message_id = "actual-current"
                records = [message_record(event, current)]
                bot._turn_context = {scope: {"history": history, "clock": bot._clock_context(),
                                            "messages": records, "image_refs": []}}
                bot._image_refs = lambda event: []
                bot._refs_for = _no_refs
                calls = []

                async def guarded_dispatch(name, args, scope="", event=None):
                    """Return explicit failure for every tool; never invoke native write handlers."""
                    calls.append({"name": name,
                                  "arguments_contain_result_marker": result in json.dumps(args, ensure_ascii=False)})
                    return {"ok": False, "error": "isolated_probe_tool_effects_forbidden"}

                bot._dispatch_tool = guarded_dispatch
                answer = await asyncio.wait_for(bot._decide_tools_first(event, scope, current, False), 600)
                selection = decode_reply(answer, records)
                marker_emitted = result in selection["text"] or any(
                    call["arguments_contain_result_marker"] for call in calls)
                positive = label == "current_match_positive"
                report["scenarios"].append({"name": label, "success": marker_emitted == positive,
                    "marker_emitted": marker_emitted, "marker_expected": positive,
                    "marker_preserved_baseline": result in history[0]["record"]["body"],
                    "history_rows": len(history), "current_message_count": len(records),
                    "current_only_source": records[0]["body"] == current,
                    "answer_chars": len(selection["text"]), "silent": not bool(selection["text"]),
                    "selection_error": selection["error"], "tool_calls": calls,
                    "answer_sha256": hashlib.sha256(answer.encode()).hexdigest()})
                output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps(report["scenarios"][-1], ensure_ascii=False), flush=True)
        finally:
            # Restore the caller environment even if model transport or fixture parsing fails.
            if previous_root is None:
                os.environ.pop("HIGAN_ROOT", None)
            else:
                os.environ["HIGAN_ROOT"] = previous_root
    report["success"] = all(row["success"] for row in report["scenarios"])
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["success"] else 1


def main():
    """Require explicit source and output paths; use production credentials read-only."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    return asyncio.run(probe(args.plugin.resolve(), args.output.resolve()))


if __name__ == "__main__":
    raise SystemExit(main())
