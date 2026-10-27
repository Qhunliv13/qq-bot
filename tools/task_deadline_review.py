"""Compare-and-set correction of an explicitly reviewed saved task deadline.

This maintenance CLI never sends a QQ message, runs a task, retries a completed
side effect, changes unrelated rows, or promotes task state from user prose.
It uses the same process/thread-locked task API as the running plugin.
"""
import argparse
from datetime import datetime
import importlib.util
import json
import math
from pathlib import Path


def review(plugin, task_id, expected_run_at, run_at, output, *, cadence=None, apply=False):
    """Change only an idle active task after exact origin and deadline review."""
    spec=importlib.util.spec_from_file_location('reviewed_native_tasks',plugin/'tasks.py')
    tasks=importlib.util.module_from_spec(spec);spec.loader.exec_module(tasks)
    for value in (expected_run_at,run_at):
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
            raise ValueError('invalid_reviewed_deadline')
    with tasks._LOCK, tasks._file_transaction_lock():
        records=tasks.listing(only_active=False)
        selected=[task for task in records if task.get('id')==task_id]
        if len(selected)!=1:raise ValueError('task_not_unique')
        task=selected[0]
        # Refuse a stale operator snapshot: never move already-running/finished
        # work or overwrite another actor's deadline or saved transport.
        if task.get('status')!='active' or task.get('execution_state','idle') not in ('idle','succeeded'):
            raise ValueError('task_not_idle_active')
        if not task.get('scope') or not task.get('origin'):raise ValueError('task_origin_missing')
        before=task.get('next_run_at')
        already=before==run_at and (not cadence or task.get('cadence')==cadence)
        if not already and before!=expected_run_at:raise ValueError('reviewed_snapshot_changed')
        if apply and not already:
            changed=tasks.update(task_id,run_at=run_at,cadence=cadence or '')
            if len(changed)!=1:raise RuntimeError('task_update_failed')
        after=next(item for item in tasks.listing(only_active=False) if item['id']==task_id)
        if apply and after['next_run_at']!=run_at:raise RuntimeError('deadline_not_persisted')
        other_before=[row for row in records if row['id']!=task_id]
        other_after=[row for row in tasks.listing(only_active=False) if row['id']!=task_id]
        if other_before!=other_after:raise RuntimeError('unrelated_task_changed')
        result={'task_id':task_id,'apply':apply,'already_correct':already,'deadline_before':before,
                'deadline_after':after['next_run_at'],'requested_run_at':run_at,
                'status':after['status'],'execution_state':after.get('execution_state'),
                'unchanged_other_tasks':len(other_after),'origin_preserved':after.get('origin')==task.get('origin'),
                'goal_preserved':after.get('goal')==task.get('goal'),
                'qq_messages_sent':0,'task_executions_triggered':0}
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def main():
    """Require an exact task ID and two explicit zone-aware reviewed timestamps."""
    parser=argparse.ArgumentParser()
    parser.add_argument('--plugin',type=Path,required=True)
    parser.add_argument('--id',required=True)
    expected=parser.add_mutually_exclusive_group(required=True)
    expected.add_argument('--expected-at')
    expected.add_argument('--expected-epoch',type=float)
    parser.add_argument('--run-at',required=True)
    parser.add_argument('--cadence',choices=['after_completion','fixed_rate'])
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    requested=datetime.fromisoformat(args.run_at)
    expected=datetime.fromisoformat(args.expected_at) if args.expected_at else None
    if requested.tzinfo is None or (expected is not None and expected.tzinfo is None):
        raise ValueError('explicit_time_zone_required')
    # JSON epochs can carry more fractional precision than datetime's microseconds.
    # Accept the exact observed numeric snapshot without weakening compare-and-set.
    expected_epoch=expected.timestamp() if expected is not None else args.expected_epoch
    print(json.dumps(review(args.plugin,args.id,expected_epoch,requested.timestamp(),args.output,
                            cadence=args.cadence,apply=args.apply),ensure_ascii=False))


if __name__=='__main__':main()
