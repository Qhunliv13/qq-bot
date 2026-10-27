"""Review one saved repeating task's cadence without running or resuming it.

Dry-run is the default. Apply uses the imported task core's transaction locks and
public update API, passing cadence only so the precise saved deadline survives.
No task IDs, business titles, transports, or execution hooks are embedded here.
"""

import argparse
import importlib.util
import json
from pathlib import Path


# Match the task core's public modes; legacy missing cadence means completion-relative.
CADENCES = ("after_completion", "fixed_rate")
STATUSES = ("active", "paused")


def review(plugin, task_id, expected_interval, cadence, output, *,
           expected_cadence="after_completion", expected_status="paused", apply=False):
    """CAS cadence only for a reviewed non-running repeat; never replace a report.

    Expected interval and status remain mandatory preconditions even on retries.
    An already-requested cadence is a no-write success, including legacy rows
    with missing cadence when their effective completion-relative mode matches.
    """
    if not task_id or type(expected_interval) is not int or expected_interval <= 0:
        raise ValueError("invalid_reviewed_task_or_interval")
    if cadence not in CADENCES or expected_cadence not in CADENCES:
        raise ValueError("invalid_reviewed_cadence")
    if expected_status not in STATUSES or type(apply) is not bool:
        raise ValueError("invalid_reviewed_status_or_apply")
    plugin, output = Path(plugin), Path(output)
    # Fail before touching task storage when the operator's report would overwrite a file.
    if output.exists():
        raise FileExistsError(output)
    spec = importlib.util.spec_from_file_location("reviewed_cadence_tasks", plugin / "tasks.py")
    tasks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tasks)
    with tasks._LOCK, tasks._file_transaction_lock():
        records = tasks.listing(only_active=False)
        selected = [row for row in records if row.get("id") == task_id]
        if len(selected) != 1:
            raise ValueError("task_not_unique")
        task = selected[0]
        # A status transition or live claim invalidates the operator's reviewed snapshot.
        if task.get("status") != expected_status or task.get("execution_state") == "running":
            raise ValueError("task_not_reviewed_nonrunning_status")
        if task.get("repeat") != "every":
            raise ValueError("task_not_repeating")
        if type(task.get("interval_s")) is not int or task["interval_s"] != expected_interval:
            raise ValueError("reviewed_interval_changed")
        current = task.get("cadence", "after_completion")
        already = current == cadence
        if not already and current != expected_cadence:
            raise ValueError("reviewed_cadence_changed")
        # Reserve a new report exclusively before applying, preventing overwrite races.
        with output.open("x", encoding="utf-8") as report:
            if apply and not already:
                changed = tasks.update(task_id=task_id, cadence=cadence)
                if len(changed) != 1:
                    raise RuntimeError("task_update_failed")
            after = tasks.listing(only_active=False)
            expected = [dict(row) for row in records]
            if apply and not already:
                # Full-row equality verifies the precise deadline, context, and every other task.
                next(row for row in expected if row.get("id") == task_id)["cadence"] = cadence
            if after != expected:
                raise RuntimeError("unexpected_task_fields_changed")
            updated = next(row for row in after if row.get("id") == task_id)
            result = {
                "task_id": task_id, "apply": apply, "already_correct": already,
                "cadence_before": current,
                "cadence_after": updated.get("cadence", "after_completion"),
                "requested_cadence": cadence, "expected_cadence": expected_cadence,
                "interval_s": updated["interval_s"], "status": updated["status"],
                "execution_state": updated.get("execution_state"),
                "deadline_before": task.get("next_run_at"),
                "deadline_after": updated.get("next_run_at"),
                "goal_preserved": updated.get("goal") == task.get("goal"),
                "origin_preserved": updated.get("origin") == task.get("origin"),
                "unchanged_other_tasks": len(records) - 1,
                "qq_messages_sent": 0, "task_executions_triggered": 0,
            }
            json.dump(result, report, ensure_ascii=False, indent=2)
    return result


def main():
    """Require explicit ID, interval and new cadence; default to paused dry-run."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin", type=Path, required=True)
    parser.add_argument("--id", required=True)
    parser.add_argument("--expected-interval", type=int, required=True)
    parser.add_argument("--expected-cadence", choices=CADENCES, default="after_completion")
    parser.add_argument("--expected-status", choices=STATUSES, default="paused")
    parser.add_argument("--cadence", choices=CADENCES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = review(args.plugin, args.id, args.expected_interval, args.cadence, args.output,
                    expected_cadence=args.expected_cadence,
                    expected_status=args.expected_status, apply=args.apply)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
