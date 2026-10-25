"""Safe repair smoke tests: public page read, deliberate JS exception, isolated scheduler, harmless long job.

This does not register accounts, submit forms, play games, send QQ messages, modify
production tasks, or restart any process. The managed job only sleeps then prints
one fixed nonce, in its own managed directory. It is cancelled in finally if needed.
"""
from pathlib import Path
import argparse
import asyncio
import importlib.util
import json
import tempfile
import types
from production_probe import load_plugin


async def run(directory, output):
    """Verify actual deployed adapters without exercising side effects of the user's task."""
    module = load_plugin(directory)
    bot = module.HiganPlugin.__new__(module.HiganPlugin)
    report = {}
    value = await bot._vm_tool("vm_eval", {"js": "({title:document.title,canvas:document.querySelectorAll('canvas').length})"})
    report["eval_value_ok"] = value.get("ok") and isinstance(value.get("value"), dict)
    error = await bot._vm_tool("vm_eval", {"js": "(()=>{throw new Error('CAPABILITY_READONLY_PROBE')})()"})
    report["eval_exception_reported"] = error.get("ok") is False and error.get("error_type") == "evaluation_error"
    undefined = await bot._vm_tool("vm_eval", {"js": "void 0"})
    report["eval_undefined_explicit"] = undefined.get("ok") and undefined.get("value_type") == "undefined"
    frame = await bot._vm_tool("vm_frame_eval", {"js": "document.title", "match": "aiapi.qhunliv13.online"})
    report["frame_eval_target_ok"] = bool(frame.get("ok"))
    # Save-only scheduler exercise uses a temporary store, never the real scheduler file.
    spec = importlib.util.spec_from_file_location("capability_tasks", directory / "tasks.py")
    tasks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tasks)
    temporary = Path(tempfile.mkdtemp(prefix="scheduler-probe-", dir=output.parent))
    tasks.ROOT = str(temporary)
    tasks.STORE = str(temporary / "tasks.json")
    tasks.SCRIPT_DIR = str(temporary)
    a = tasks.add("isolated-A", "probe", "probe", repeat="once", origin="probe")
    b = tasks.add("isolated-B", "probe", "probe", repeat="once", origin="probe")
    first = tasks.claim(a["id"])
    tasks.mark(a["id"], "done", claim_id=first["claim_id"])
    rows = {row["id"]: row for row in tasks.listing(only_active=False)}
    report["task_specific_ack"] = rows[a["id"]]["status"] == "done" and rows[b["id"]]["status"] == "active"
    report["duplicate_claim_prevented"] = tasks.claim(a["id"]) is None
    bot._tasks_mod = lambda: tasks
    bot._tool_evidence_store = types.SimpleNamespace(recent=lambda *args: [])
    job_id = None
    terminal = False
    try:
        start = await bot._dispatch_tool("vm_job_start", {"code": "import time\ntime.sleep(33)\nprint('CAPABILITY_JOB_OK')", "lang": "python"}, "probe")
        job_id = start.get("job_id")
        report["job_start_ok"] = bool(start.get("ok") and job_id)
        if job_id:
            early = await bot._dispatch_tool("vm_job_status", {"job_id": job_id}, "probe")
            report["job_query_ok"] = early.get("ok") and early.get("status") in ("starting", "running", "completed")
            # A single >30s job validates detachment from the VM synchronous request deadline.
            await asyncio.sleep(35)
            final = await bot._dispatch_tool("vm_job_status", {"job_id": job_id}, "probe")
            terminal = final.get("status") in ("completed", "failed", "cancelled", "lost")
            report["job_survived_30_seconds"] = final.get("ok") and final.get("status") == "completed" and "CAPABILITY_JOB_OK" in final.get("output", "")
    finally:
        if job_id and not terminal:
            await bot._dispatch_tool("vm_job_cancel", {"job_id": job_id}, "probe")
    report["qq_messages_sent"] = 0
    report["vm_service_restart"] = False
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    checks = [value for key, value in report.items() if key not in ("qq_messages_sent", "vm_service_restart")]
    return 0 if checks and all(checks) else 1


def main():
    """Require explicit staged/deployed plugin and report paths."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    return asyncio.run(run(args.plugin.resolve(), args.output.resolve()))


if __name__ == "__main__":
    raise SystemExit(main())
