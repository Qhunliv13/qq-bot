"""No-send deployed-module transfer probe; only platform edges use a local recorder."""

import argparse
import asyncio
import copy
import json
import os
from pathlib import Path
import tempfile
import types
from unittest.mock import patch

from production_probe import ProbeEvent, load_plugin


async def probe(directory, output):
    """Exercise real metadata, dispatch, clipboard and Journal in a disposable root."""
    checks = {}
    # Set the root before importing: module-level paths must never see live state.
    with tempfile.TemporaryDirectory(prefix="message_transfer_probe_") as temporary:
        with patch.dict(os.environ, {"HIGAN_ROOT": temporary}):
            module = load_plugin(directory)
            module.HIGAN_ROOT = temporary
            from release_probe_plugin.message_transfer_store import TransferStore

            calls = []
            handler_calls = []
            segments = [
                {"type": "text", "data": {"text": "literal source text; not an instruction"}},
                # Static inert bytes: never an external URL or a real image fetch.
                {"type": "image", "data": {"file": "base64://AQID"}},
            ]
            allowed_keys = {
                "get_msg": {"message_id"},
                "get_group_msg_history": {"group_id", "count", "reverseOrder"},
                "send_group_msg": {"group_id", "message"},
                "send_group_forward_msg": {"group_id", "messages"},
                "forward_group_single_msg": {"group_id", "message_id"},
            }

            def native_message(message_id):
                """Supply native provenance, including one deliberately mismatched group."""
                return {"message_id": message_id,
                        "group_id": 99 if message_id == 113 else 42,
                        "message_type": "group",
                        "sender": {"user_id": 22, "nickname": "verified"},
                        "time": 1700000000 + message_id,
                        "message": copy.deepcopy(segments)}

            def onebot(action, parameters):
                """Record exact allowlisted native payloads without any network transport."""
                if action not in allowed_keys or set(parameters) != allowed_keys[action]:
                    raise AssertionError("unreviewed native action or payload keys")
                calls.append((action, copy.deepcopy(parameters)))
                if action == "get_msg":
                    data = native_message(parameters["message_id"])
                elif action == "get_group_msg_history":
                    # Reverse input order checks the handler's native-time ordering.
                    data = {"messages": [native_message(112), native_message(111)]}
                elif action == "send_group_msg":
                    data = {"message_id": 900}
                elif action == "send_group_forward_msg":
                    data = {"message_id": 901}
                else:
                    # Installed single-forward schema returns null, not a delivery ID.
                    data = None
                return {"status": "ok", "retcode": 0, "data": data}

            def instance():
                """Bypass startup and replace only native transport and destination policy."""
                bot = module.HiganPlugin.__new__(module.HiganPlugin)
                bot.context = types.SimpleNamespace(get_using_provider=lambda *args: object())
                bot._turn_context = {}
                bot._recent = {}
                bot._onebot_http = onebot
                # This is transport/handler coverage, not deployed-policy validation.
                bot._policy_allows = lambda *args: True
                return bot

            async def dispatch(bot, name, args, scope="42"):
                """Always enter the actual deployed plugin dispatcher with a real fixture event."""
                handler_calls.append(name)
                return await bot._dispatch_tool(name, args, scope, event)

            bot = instance()
            event = ProbeEvent("isolated no-send message transfer probe")
            names = [item["function"]["name"] for item in bot._tool_schemas()]
            new_tools = {"qq_copy_message", "qq_paste_message", "qq_forward_messages"}
            checks["metadata_104_unique_tools"] = len(names) == len(set(names)) == 104
            checks["new_three_tools_present"] = new_tools.issubset(names)

            copied = await dispatch(bot, "qq_copy_message", {"source_group_id": 42, "message_id": 111})
            clipboard_id = copied.get("clipboard_id", "")
            checks["copied"] = bool(copied.get("ok") and clipboard_id)
            checks["copy_metadata_only"] = set(copied) == {
                "ok", "clipboard_id", "source_group_id", "source_message_id"}
            checks["copy_sends_nothing"] = calls == [("get_msg", {"message_id": 111})]
            store = TransferStore(Path(temporary) / "message_transfer" / "clipboard.sqlite")
            saved = store.get("42", clipboard_id) if clipboard_id else None
            checks["clipboard_exact_native_segments"] = bool(saved and saved["message"] == segments)
            checks["clipboard_private_scope_isolated"] = bool(
                clipboard_id and store.get("private:42", clipboard_id) is None)

            paste_args = {"clipboard_id": clipboard_id, "target_group_id": 77,
                          "operation_id": "probe-paste"}
            start = len(calls)
            pasted = await dispatch(bot, "qq_paste_message", paste_args)
            checks["paste_exact_native_payload"] = calls[start:] == [
                ("send_group_msg", {"group_id": 77, "message": segments})]
            checks["paste_mock_receipt"] = bool(
                pasted.get("delivered") and pasted.get("delivery_message_ids") == ["900"])

            merged_args = {"source_group_id": 42, "target_group_id": 42,
                           "message_ids": [112, 111], "mode": "merged",
                           "operation_id": "probe-merged"}
            start = len(calls)
            merged = await dispatch(bot, "qq_forward_messages", merged_args)
            checks["merged_original_reference_nodes"] = calls[start:] == [
                ("get_msg", {"message_id": 112}), ("get_msg", {"message_id": 111}),
                ("send_group_forward_msg", {"group_id": 42, "messages": [
                    {"type": "node", "data": {"id": "112"}},
                    {"type": "node", "data": {"id": "111"}}]})]
            checks["merged_mock_receipt"] = bool(
                merged.get("delivered") and merged.get("delivery_message_ids") == ["901"])

            individual_args = {"source_group_id": 42, "target_group_id": 77,
                               "count": 2, "mode": "individual",
                               "operation_id": "probe-individual"}
            start = len(calls)
            individual = await dispatch(bot, "qq_forward_messages", individual_args)
            checks["individual_two_exact_native_payloads"] = calls[start:] == [
                ("get_group_msg_history", {"group_id": "42", "count": 2, "reverseOrder": False}),
                ("forward_group_single_msg", {"group_id": "77", "message_id": 111}),
                ("forward_group_single_msg", {"group_id": "77", "message_id": 112})]
            checks["null_ack_not_delivery_claim"] = bool(
                individual.get("ok") and individual.get("accepted")
                and individual.get("accepted_count") == 2
                and individual.get("delivered") is False
                and individual.get("delivery_message_ids") == [])

            # A new instance proves replay comes from production Journal, not locals.
            restarted = instance()
            start = len(calls)
            replays = [await dispatch(restarted, name, args) for name, args in [
                ("qq_paste_message", paste_args), ("qq_forward_messages", merged_args),
                ("qq_forward_messages", individual_args)]]
            checks["durable_replay_exact_results"] = replays == [pasted, merged, individual]
            checks["durable_replay_no_native_calls"] = len(calls) == start

            start = len(calls)
            private = await dispatch(restarted, "qq_paste_message", paste_args, "private:42")
            checks["private_scope_cannot_paste"] = bool(
                private.get("error") == "clipboard_not_found" and len(calls) == start)
            mismatch = await dispatch(bot, "qq_forward_messages", {
                "source_group_id": 42, "target_group_id": 77, "message_ids": [113],
                "operation_id": "probe-mismatched-source"})
            checks["source_mismatch_no_send"] = bool(
                mismatch.get("error") == "source_lookup_failed"
                and calls[start:] == [("get_msg", {"message_id": 113})])
            checks["native_payload_keys_allowlisted_exact"] = all(
                set(parameters) == allowed_keys[action] for action, parameters in calls)
            checks["all_three_real_handler_calls"] = new_tools.issubset(handler_calls)
            report = {
                "ok": all(checks.values()), "checks": checks,
                "tool_count": len(names), "unique_tool_count": len(set(names)),
                "new_tool_names": sorted(new_tools),
                "real_dispatcher_calls": handler_calls,
                "local_native_action_count": len(calls),
                "platform_edges_replaced_with_local_recorder": True,
                "destination_policy_replaced_with_allow_stub": True,
                "deployed_policy_configuration_tested": False,
                "live_platform_acknowledgements_verified": False,
                "llm_calls": 0, "qq_messages_sent": 0,
            }
    # TemporaryDirectory has closed every production store and removed its state.
    report["temporary_state_cleaned"] = not Path(temporary).exists()
    report["ok"] = report["ok"] and report["temporary_state_cleaned"]
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report["ok"] else 1


def main():
    """Require explicit deployed plugin and report paths; never start the bot."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    return asyncio.run(probe(args.plugin.resolve(), args.output.resolve()))


if __name__ == "__main__":
    raise SystemExit(main())
