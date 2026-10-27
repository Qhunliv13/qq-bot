"""Exercise reviewed deadline corrections against real task APIs in scratch storage."""

import importlib.util
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest


# A reviewed 13:00 deadline was accidentally saved forty seconds too early.
REVIEWED_AT = datetime.fromisoformat("2026-10-27T13:00:00+08:00").timestamp()
EARLY_AT = REVIEWED_AT - 40


def _load_module(name, path):
    """Import one standalone file without loading QQ transports or plugin startup."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sandbox(tmp_path):
    """Copy the real task implementation, overriding STORE before any API call.

    review imports tasks afresh internally, so patching an earlier imported
    module would not isolate its writes. The appended constant isolates every
    import while retaining the actual public API and nested store locks.
    """
    root = Path(__file__).resolve().parents[1]
    plugin = tmp_path / "native plugin"
    plugin.mkdir()
    store = tmp_path / "scratch tasks.json"
    source = (root / "plugin" / "tasks.py").read_text(encoding="utf-8")
    (plugin / "tasks.py").write_text(
        source + "\n# Test-only storage; never use the native default.\nSTORE = "
        + repr(str(store)) + "\n", encoding="utf-8")
    tasks = _load_module("deadline_test_tasks", plugin / "tasks.py")
    review = _load_module("deadline_test_review", root / "tools" / "task_deadline_review.py")
    target = tasks.add(
        "reviewed 13:00 task", "Keep this existing goal unchanged", "private:test-user",
        run_at=EARLY_AT, origin="private", brief="saved context",
        resume_prompt="Continue the existing task, do not create another one")
    other = tasks.add("unrelated task", "Do not edit", "test-room", run_at=REVIEWED_AT + 600,
                      origin="group")
    return SimpleNamespace(tasks=tasks, review=review.review, plugin=plugin, store=store,
                           output=tmp_path / "review output.json", target=target, other=other)


def _review(sandbox, **kwargs):
    """Use the CLI's public callable with the exact reviewed snapshot."""
    return sandbox.review(sandbox.plugin, sandbox.target["id"], EARLY_AT,
                          REVIEWED_AT, sandbox.output, **kwargs)


def _patch_target(sandbox, **changes):
    """Seed historical scheduler states without executing or delivering any task."""
    rows = json.loads(sandbox.store.read_text(encoding="utf-8"))
    for row in rows:
        if row["id"] == sandbox.target["id"]:
            row.update(changes)
    sandbox.store.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


def test_dry_run_preserves_exact_store_and_due_time(sandbox):
    """A preview must report the proposed deadline without changing persisted bytes."""
    before = sandbox.store.read_bytes()
    result = _review(sandbox)
    assert sandbox.store.read_bytes() == before
    assert result["apply"] is False
    assert result["already_correct"] is False
    assert result["deadline_before"] == result["deadline_after"] == EARLY_AT
    assert result["requested_run_at"] == REVIEWED_AT
    assert json.loads(sandbox.output.read_text(encoding="utf-8")) == result


def test_apply_corrects_only_reviewed_deadline_and_preserves_context(sandbox):
    """Correct the forty-second error without rewriting goals, origins, or other rows."""
    before = sandbox.tasks.listing(only_active=False)
    result = _review(sandbox, apply=True)
    after = sandbox.tasks.listing(only_active=False)
    expected = [dict(row) for row in before]
    expected[0]["next_run_at"] = REVIEWED_AT
    assert after == expected
    assert result["deadline_before"] == EARLY_AT
    assert result["deadline_after"] == REVIEWED_AT
    assert result["unchanged_other_tasks"] == 1
    assert result["origin_preserved"] is True
    assert result["goal_preserved"] is True
    assert result["qq_messages_sent"] == result["task_executions_triggered"] == 0
    assert sandbox.tasks.due(now=REVIEWED_AT - 1) == []
    assert [row["id"] for row in sandbox.tasks.due(now=REVIEWED_AT)] == [sandbox.target["id"]]
    assert json.loads(sandbox.output.read_text(encoding="utf-8")) == result


def test_repeated_apply_is_idempotent_even_with_original_snapshot(sandbox):
    """An operator retry must recognize the already-correct deadline and avoid writes."""
    _review(sandbox, apply=True)
    before = sandbox.store.read_bytes()
    result = _review(sandbox, apply=True)
    assert result["already_correct"] is True
    assert result["deadline_before"] == result["deadline_after"] == REVIEWED_AT
    assert sandbox.store.read_bytes() == before


def test_apply_can_explicitly_review_cadence(sandbox):
    """Change cadence only when explicitly supplied, preserving every other field."""
    before = sandbox.tasks.listing(only_active=False)
    _review(sandbox, cadence="fixed_rate", apply=True)
    before[0].update(next_run_at=REVIEWED_AT, cadence="fixed_rate")
    assert sandbox.tasks.listing(only_active=False) == before
    assert _review(sandbox, cadence="fixed_rate", apply=True)["already_correct"] is True


def test_stale_snapshot_rejected_without_writing(sandbox):
    """A concurrent deadline change cannot be overwritten using the old snapshot."""
    sandbox.tasks.update(sandbox.target["id"], run_at=EARLY_AT + 10)
    before = sandbox.store.read_bytes()
    with pytest.raises(ValueError, match="^reviewed_snapshot_changed$"):
        _review(sandbox, apply=True)
    assert sandbox.store.read_bytes() == before
    assert not sandbox.output.exists()


@pytest.mark.parametrize("changes", [
    {"execution_state": "running"},
    {"status": "paused"},
    {"status": "done"},
    {"status": "cancelled"},
])
def test_non_idle_or_inactive_task_rejected(sandbox, changes):
    """Do not reschedule running work, resume paused work, or revive terminal tasks."""
    _patch_target(sandbox, **changes)
    before = sandbox.store.read_bytes()
    with pytest.raises(ValueError, match="^task_not_idle_active$"):
        _review(sandbox, apply=True)
    assert sandbox.store.read_bytes() == before
    assert not sandbox.output.exists()


@pytest.mark.parametrize("changes", [{"origin": ""}, {"scope": ""}])
def test_unknown_origin_or_scope_rejected(sandbox, changes):
    """Missing saved routing evidence must fail closed instead of guessing a recipient."""
    _patch_target(sandbox, **changes)
    before = sandbox.store.read_bytes()
    with pytest.raises(ValueError, match="^task_origin_missing$"):
        _review(sandbox, apply=True)
    assert sandbox.store.read_bytes() == before
    assert not sandbox.output.exists()


def test_missing_exact_id_rejected_without_touching_other_tasks(sandbox):
    """An unknown identifier must not fall back to titles or another saved task."""
    before = sandbox.store.read_bytes()
    with pytest.raises(ValueError, match="^task_not_unique$"):
        sandbox.review(sandbox.plugin, "not-a-task", EARLY_AT, REVIEWED_AT,
                       sandbox.output, apply=True)
    assert sandbox.store.read_bytes() == before
    assert not sandbox.output.exists()
