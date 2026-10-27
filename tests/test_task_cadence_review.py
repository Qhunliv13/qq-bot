"""Exercise cadence-only review using real task APIs and isolated scratch stores."""

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


# Preserve a JSON epoch's full binary precision, not an ISO microsecond round-trip.
DEADLINE = 1793072165.0304322


def _load(name, path):
    """Load a standalone module without starting plugin transports or execution."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sandbox(tmp_path):
    """Append a test-only STORE override so fresh internal imports stay isolated."""
    root = Path(__file__).resolve().parents[1]
    plugin = tmp_path / "native plugin"
    plugin.mkdir()
    store = tmp_path / "scratch tasks.json"
    source = (root / "plugin" / "tasks.py").read_text(encoding="utf-8")
    (plugin / "tasks.py").write_text(
        source + "\n# Test-only storage; never touch the native default.\nSTORE = "
        + repr(str(store)) + "\n", encoding="utf-8")
    tasks = _load("cadence_test_tasks", plugin / "tasks.py")
    tool = _load("cadence_test_review", root / "tools" / "task_cadence_review.py")
    target = tasks.add("generic periodic task", "Keep existing goal", "private:test",
                       interval_s=60, repeat="every", run_at=DEADLINE, origin="private")
    tasks.add("unrelated task", "Untouched", "test-room", run_at=DEADLINE + 600,
              origin="group")
    rows = json.loads(store.read_text(encoding="utf-8"))
    # Model a paused legacy row lacking cadence, with additional opaque context preserved.
    rows[0].pop("cadence")
    rows[0].update(status="paused", arbitrary_context={"text": "原样保留", "value": False})
    store.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return SimpleNamespace(tasks=tasks, tool=tool, plugin=plugin, store=store,
                           target=target, output=tmp_path / "report.json")


def _review(sandbox, **kwargs):
    """Supply the reviewed generic snapshot while allowing individual CAS variations."""
    arguments = dict(plugin=sandbox.plugin, task_id=sandbox.target["id"],
                     expected_interval=60, cadence="fixed_rate", output=sandbox.output)
    arguments.update(kwargs)
    return sandbox.tool.review(**arguments)


def _patch(sandbox, **changes):
    """Seed scheduler states directly in scratch storage without running any work."""
    rows = json.loads(sandbox.store.read_text(encoding="utf-8"))
    rows[0].update(changes)
    sandbox.store.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


def test_dry_run_is_byte_preserving(sandbox):
    """Default preview reports the change but leaves the original store byte-identical."""
    before = sandbox.store.read_bytes()
    result = _review(sandbox)
    assert sandbox.store.read_bytes() == before
    assert result["apply"] is False
    assert result["already_correct"] is False
    assert result["cadence_before"] == result["cadence_after"] == "after_completion"
    assert result["requested_cadence"] == "fixed_rate"
    assert result["deadline_before"] == result["deadline_after"] == DEADLINE
    assert json.loads(sandbox.output.read_text(encoding="utf-8")) == result


def test_apply_changes_only_cadence(sandbox):
    """Keep deadline, goal, origin, interval, status and all unrelated fields exact."""
    expected = sandbox.tasks.listing(only_active=False)
    expected[0]["cadence"] = "fixed_rate"
    result = _review(sandbox, apply=True)
    assert sandbox.tasks.listing(only_active=False) == expected
    assert result["status"] == "paused"
    assert result["interval_s"] == 60
    assert result["deadline_before"] == result["deadline_after"] == DEADLINE
    assert result["goal_preserved"] and result["origin_preserved"]
    assert result["unchanged_other_tasks"] == 1
    assert result["qq_messages_sent"] == result["task_executions_triggered"] == 0
    assert sandbox.tasks.due(now=DEADLINE + 1) == []


def test_repeat_apply_is_byte_idempotent(sandbox):
    """Retry with the original expected mode recognizes the new mode without writing."""
    _review(sandbox, apply=True)
    before = sandbox.store.read_bytes()
    result = _review(sandbox, apply=True, output=sandbox.output.with_name("retry.json"))
    assert result["already_correct"] is True
    assert sandbox.store.read_bytes() == before


@pytest.mark.parametrize("changes,error", [
    ({"status": "active"}, "task_not_reviewed_nonrunning_status"),
    ({"status": "done"}, "task_not_reviewed_nonrunning_status"),
    ({"status": "cancelled"}, "task_not_reviewed_nonrunning_status"),
    ({"status": False}, "task_not_reviewed_nonrunning_status"),
    ({"execution_state": "running"}, "task_not_reviewed_nonrunning_status"),
    ({"repeat": "once"}, "task_not_repeating"),
    ({"interval_s": 61}, "reviewed_interval_changed"),
    ({"interval_s": False}, "reviewed_interval_changed"),
    ({"cadence": "unknown"}, "reviewed_cadence_changed"),
    ({"cadence": None}, "reviewed_cadence_changed"),
])
def test_stale_or_unsafe_snapshot_rejected(sandbox, changes, error):
    """Never override changed state, non-repeating work, malformed cadence or claims."""
    _patch(sandbox, **changes)
    before = sandbox.store.read_bytes()
    with pytest.raises(ValueError, match="^" + error + "$"):
        _review(sandbox, apply=True)
    assert sandbox.store.read_bytes() == before
    assert not sandbox.output.exists()


@pytest.mark.parametrize("changes", [{"status": "active"}, {"interval_s": 61}])
def test_already_correct_still_checks_other_preconditions(sandbox, changes):
    """Idempotency cannot bypass a status transition or interval changed by another actor."""
    _patch(sandbox, cadence="fixed_rate", **changes)
    before = sandbox.store.read_bytes()
    with pytest.raises(ValueError):
        _review(sandbox, apply=True)
    assert sandbox.store.read_bytes() == before


def test_explicit_active_snapshot_is_supported_without_resuming(sandbox):
    """A deliberately reviewed active task keeps its active state and exact deadline."""
    _patch(sandbox, status="active")
    expected = sandbox.tasks.listing(only_active=False)
    expected[0]["cadence"] = "fixed_rate"
    _review(sandbox, apply=True, expected_status="active")
    assert sandbox.tasks.listing(only_active=False) == expected


def test_explicit_reverse_cas_and_stale_mode(sandbox):
    """Both supported modes work, while a third effective snapshot fails closed."""
    _patch(sandbox, cadence="fixed_rate")
    _review(sandbox, apply=True, cadence="after_completion", expected_cadence="fixed_rate")
    assert sandbox.tasks.listing(only_active=False)[0]["cadence"] == "after_completion"
    before = sandbox.store.read_bytes()
    with pytest.raises(ValueError, match="^reviewed_cadence_changed$"):
        _review(sandbox, apply=True, expected_cadence="fixed_rate",
                output=sandbox.output.with_name("stale.json"))
    assert sandbox.store.read_bytes() == before


def test_unknown_id_rejected(sandbox):
    """Exact identifiers never fall back to task titles or another row."""
    before = sandbox.store.read_bytes()
    with pytest.raises(ValueError, match="^task_not_unique$"):
        _review(sandbox, apply=True, task_id="unknown-id")
    assert sandbox.store.read_bytes() == before


def test_existing_output_is_never_overwritten_or_applied(sandbox):
    """Reject an existing output before mutation, preserving its bytes and the store."""
    sandbox.output.write_text("existing evidence", encoding="utf-8")
    before = sandbox.store.read_bytes()
    with pytest.raises(FileExistsError):
        _review(sandbox, apply=True)
    assert sandbox.output.read_text(encoding="utf-8") == "existing evidence"
    assert sandbox.store.read_bytes() == before


@pytest.mark.parametrize("value", [False, 60.0, 0, -1])
def test_expected_interval_requires_positive_integer(sandbox, value):
    """Reject boolean, coerced or nonpositive reviewed intervals before reading tasks."""
    before = sandbox.store.read_bytes()
    with pytest.raises(ValueError, match="^invalid_reviewed_task_or_interval$"):
        _review(sandbox, expected_interval=value, apply=True)
    assert sandbox.store.read_bytes() == before


@pytest.mark.parametrize("apply", [False, True])
def test_cli_explicit_arguments_and_default_dry_run(sandbox, monkeypatch, capsys, apply):
    """CLI requires generic identifiers and interval; only --apply enables writing."""
    arguments = ["task_cadence_review.py", "--plugin", str(sandbox.plugin),
                 "--id", sandbox.target["id"], "--expected-interval", "60",
                 "--cadence", "fixed_rate", "--output", str(sandbox.output)]
    if apply:
        arguments.append("--apply")
    monkeypatch.setattr(sys, "argv", arguments)
    before = sandbox.store.read_bytes()
    sandbox.tool.main()
    result = json.loads(capsys.readouterr().out)
    assert result["apply"] is apply
    assert result["status"] == "paused"
    assert result["deadline_after"] == DEADLINE
    if not apply:
        assert sandbox.store.read_bytes() == before
    assert json.loads(sandbox.output.read_text(encoding="utf-8")) == result
