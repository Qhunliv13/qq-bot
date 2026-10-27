"""Offline production fact/queue probe; no platform, network, or model access."""

import argparse
import ast
import asyncio
import copy
import importlib
import inspect
import json
import os
from pathlib import Path
import tempfile
import textwrap
import uuid

from production_probe import ProbeEvent, load_plugin


class OwnerProbeEvent(ProbeEvent):
    """Give synthetic messages unique metadata without granting live authority."""

    def __init__(self, text):
        super().__init__(text)
        self.message_obj.message_id = uuid.uuid4().hex
        self.message_obj.raw_message = text


def forbid_native(*_args, **_kwargs):
    """Fail before any native HTTP IO, even if a fixture takes an unexpected path."""
    raise RuntimeError("native HTTP forbidden in offline fact/queue probe")


async def queue_probe(bot):
    """Exercise the real lock-wait batching worker, substituting only turn execution."""
    first = OwnerProbeEvent("queue fixture first")
    second = OwnerProbeEvent("queue fixture second")
    bot._pending = {"42": [(first, first.text, False, False, ())]}
    bot._busy = {"42": True}
    bot._execution_lock = asyncio.Lock()
    bot._turn_context = {}
    bot._background_tasks = set()
    captured = []

    async def record_turn(_event, scope, _text, _has_image, _woken):
        """Capture batch attribution only; never execute a model or send a reply."""
        captured.append(copy.deepcopy(bot._turn_context[scope]["messages"]))

    bot._handle_one = record_turn
    await bot._execution_lock.acquire()
    worker = bot._start_background(bot._drain_pending("42"))
    # One event-loop turn lets the actual worker block on the held execution lock.
    await asyncio.sleep(0)
    assert not worker.done() and worker in bot._background_tasks
    bot._pending["42"].append((second, second.text, False, False, ()))
    bot._execution_lock.release()
    await asyncio.wait_for(worker, timeout=5)
    await asyncio.sleep(0)  # Run the native ownership-removal completion callback.
    assert len(captured) == 1 and len(captured[0]) == 2
    assert [row["id"] for row in captured[0]] == [
        first.message_obj.message_id, second.message_obj.message_id]
    assert not bot._pending["42"] and bot._busy["42"] is False
    assert worker.done() and worker not in bot._background_tasks
    return {"waiting_messages_batched": 2, "native_queue_worker_verified": True,
            "native_worker_busy_cleared": True, "owned_task_registry_clean": True}


async def probe(directory):
    """Import production code only after redirecting all plugin state into temp storage."""
    previous_root = os.environ.get("HIGAN_ROOT")
    with tempfile.TemporaryDirectory(prefix="fact_queue_probe_") as temporary:
        os.environ["HIGAN_ROOT"] = temporary
        try:
            module = load_plugin(directory)
            package = module.__package__
            memory = importlib.import_module(package + ".memory")
            delivery = importlib.import_module(package + ".task_delivery")
            delegated = importlib.import_module(package + ".delegated_runner")
            store = memory.Memory(str(Path(temporary) / "memory" / "memory.json"))
            store.add_fact("fixture public first", "42")
            store.add_fact("fixture public second", "42")
            store.add_fact("fixture private", "private:99", categories=["private-label"])
            rows = store.list_facts(None, current_scope="42")
            first, second = rows[0]["id"], rows[1]["id"]
            before = copy.deepcopy(rows[0])
            bot = module.HiganPlugin.__new__(module.HiganPlugin)
            # This bypass is deliberate: live policy and framework admission are not tested.
            bot._policy_allows = lambda _event, _scope: True
            bot._onebot_http = forbid_native
            event = OwnerProbeEvent("offline owner fixture")
            categories = await bot._dispatch_tool("facts_categories", {}, "42", event)
            assert categories["ok"] and categories["total"] == 2
            args = {"ids": [first], "categories": ["任意分类"]}
            applied = await bot._dispatch_tool("facts_classify", args, "42", event)
            assert applied["ok"] and applied["updated"] == 1
            replay = await bot._dispatch_tool("facts_classify", args, "42", event)
            assert replay["ok"] and replay["updated"] == 0
            denied = await bot._dispatch_tool("facts_classify", args, "42", None)
            assert denied["ok"] is False
            removed = await bot._dispatch_tool("facts_delete", {"id": second}, "42", event)
            assert removed == {"ok": True, "removed": 1}
            visible = await bot._dispatch_tool("facts_list", {}, "42", event)
            assert visible["ok"] and len(visible["facts"]) == 1
            after = visible["facts"][0]
            assert after["categories"] == ["任意分类"]
            assert {k: v for k, v in after.items() if k != "categories"} == {
                k: v for k, v in before.items() if k != "categories"}
            assert after["source_scope"] == "42"
            persisted = memory.Memory(store.path).load().list_facts(None)
            assert len(persisted) == 2 and persisted[-1]["source_scope"] == "private:99"
            catalog = bot._tool_schemas()
            names = {item["function"]["name"] for item in catalog}
            readonly = delegated.READ_ONLY_TOOLS
            reads = {"facts_list", "facts_search", "facts_categories"}
            assert reads <= names and reads <= readonly
            assert not {"facts_classify", "facts_delete", "facts_edit", "remember"} & readonly
            worker_catalogs = []

            async def fixture_model(_system, _prompt, _images, **kwargs):
                """Capture the actual worker catalog without any provider/model transport."""
                worker_catalogs.append(kwargs["tool_schemas"])
                return json.dumps({"reply_to": [], "text": "offline complete"}), []

            async def empty_inbox():
                """Supply no manager updates to the isolated native worker."""
                return []

            bot.persona = "offline fixture"
            bot._llm_tools = fixture_model
            bot._turn_context = {}
            await delegated.run_worker(bot, "offline catalog inspection", "42", empty_inbox)
            advertised = {item["function"]["name"] for item in worker_catalogs[0]}
            assert reads <= advertised
            assert not {"facts_classify", "facts_delete", "facts_edit", "remember"} & advertised
            # Inspect native ingress structurally, without pretending a partial event is live ingress.
            ingress = ast.parse(textwrap.dedent(inspect.getsource(module.HiganPlugin.on_message)))
            owned_call = any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                             and node.func.attr == "_start_background" for node in ast.walk(ingress))
            assert owned_call
            task = {"id": "fixture-task", "claim_id": "fixture-claim", "status": "active"}
            completed = dict(task, status="completed")
            assert delivery.current_task(task, [completed]) == completed
            assert delivery.task_active(task, [completed]) is False
            assert delivery.current_task(task, [dict(task, claim_id="other")]) is None
            assert delivery.current_task(task, [dict(task, id="other")]) is None
            assert delivery.current_task(task, [task]) == task
            assert delivery.task_active(task, [task]) is True
            report = {"ok": True, "offline_only": True, "fact_routes_verified": True,
                      "classification_idempotent": True, "fact_metadata_preserved": True,
                      "private_scope_hidden": True, "eventless_mutation_denied": True,
                      "exact_delete_removed": 1, "worker_readonly_catalog_verified": True,
                      "tool_count": len(catalog), "tool_count_is_106": len(catalog) == 106,
                      "catalog_dynamic_capability_count": len(names),
                      "task_claim_binding_verified": True, "stale_completed_task_inactive": True,
                      "owned_ingress_worker_present": hasattr(bot, "_drain_pending"),
                      "ingress_owned_start_call_present": owned_call,
                      "ingress_adapter_live_tested": False, "live_policy_tested": False,
                      "native_http_calls": 0, "model_calls": 0, "platform_sends": 0}
            report.update(await queue_probe(bot))
            return report
        finally:
            # Avoid leaking the fixture root to callers running this probe in-process.
            if previous_root is None:
                os.environ.pop("HIGAN_ROOT", None)
            else:
                os.environ["HIGAN_ROOT"] = previous_root


def main():
    """Accept explicit plugin/output paths and emit metadata, never fact content or IDs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = asyncio.run(probe(args.plugin.resolve()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
