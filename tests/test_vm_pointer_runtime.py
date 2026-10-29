"""Offline deterministic pointer primitive tests; never launch a process or socket."""

import copy
import importlib.util
from pathlib import Path

import pytest


_SPEC = importlib.util.spec_from_file_location(
    "pointer_runtime_subject", Path(__file__).resolve().parents[1] / "plugin" / "vm_pointer_runtime.py")
runtime = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runtime)


def action():
    """Build an explicit finite path with a stationary hold followed by movement."""
    return {"version": 1, "button": "left", "width": 80, "height": 60,
            "points": [{"x": 10, "y": 20, "t_ms": 0},
                       {"x": 10, "y": 20, "t_ms": 100},
                       {"x": 40, "y": 30, "t_ms": 200}]}


class Adapter:
    """Record exact commands and virtual monotonic time, with injected failures."""

    def __init__(self, failure=None, result=1):
        self.commands = []
        self.timeouts = []
        self.sleeps = []
        self.now = 10.0
        self.failure = failure
        self.result = result
        self.mutate = None

    def runner(self, argv, *, timeout_s):
        """Return only an exit code and mutate caller input to test frozen copying."""
        self.commands.append(argv)
        self.timeouts.append(timeout_s)
        if self.mutate:
            self.mutate()
        if len(self.commands) == self.failure:
            if isinstance(self.result, Exception):
                raise self.result
            return self.result
        return 0

    def sleep(self, seconds):
        """Advance virtual time without real blocking."""
        self.sleeps.append(seconds)
        self.now += seconds

    def clock(self):
        """Expose the controlled monotonic clock."""
        return self.now

    def execute(self, request):
        """Invoke the primitive exclusively with fake runtime capabilities."""
        return runtime.execute_pointer_path(request, runner=self.runner, sleep=self.sleep, clock=self.clock)


def test_exact_path_timing_immutable_and_no_shell():
    """All points and timing come from the copied action, never later caller mutation."""
    request = action()
    adapter = Adapter()
    adapter.mutate = lambda: request["points"][2].update(x=79, t_ms=9000)
    assert adapter.execute(request) == {"ok": True, "version": 1, "points_executed": 3}
    assert adapter.commands == [
        ("xdotool", "mousemove", "10", "20"),
        ("xdotool", "mousedown", "1"),
        ("xdotool", "mousemove", "10", "20"),
        ("xdotool", "mousemove", "40", "30"),
        ("xdotool", "mouseup", "1")]
    assert adapter.sleeps == pytest.approx([0.1, 0.1])
    assert all(0 < value <= 1 for value in adapter.timeouts)
    frozen = runtime.canonical_pointer_action(action())
    with pytest.raises(AttributeError):
        frozen.points[0].x = 3


@pytest.mark.parametrize("field,value", [
    ("version", True), ("version", 2), ("button", "right"), ("width", True),
    ("width", 0), ("height", 16385), ("points", []), ("points", "bad"),
    ("extra", 1), ("points", [{"x": 1, "y": 1, "t_ms": 0}] * 129)])
def test_invalid_top_level_has_zero_runtime_calls(field, value):
    """Unknown versions/keys and oversized payloads fail before capabilities run."""
    request = action()
    request[field] = value
    adapter = Adapter()
    assert adapter.execute(request) == {"ok": False, "error": "invalid_pointer_action"}
    assert adapter.commands == [] and adapter.sleeps == []


@pytest.mark.parametrize("index,field,value", [
    (0, "x", True), (0, "x", "1; touch unsafe"), (0, "x", -1),
    (0, "x", 80), (0, "y", 60), (0, "t_ms", 1), (1, "t_ms", 0),
    (1, "t_ms", 10001), (2, "t_ms", 99), (1, "extra", 1)])
def test_invalid_points_never_move(index, field, value):
    """Reject coordinate overflow, coercion, injections, and ambiguous schedules."""
    request = action()
    request["points"][index][field] = value
    adapter = Adapter()
    assert adapter.execute(request)["error"] == "invalid_pointer_action"
    assert adapter.commands == []


@pytest.mark.parametrize("failure", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("result", [1, True, None, {"ok": True}, RuntimeError("private-token")])
def test_every_failed_command_prevents_success_and_releases(failure, result):
    """No retries or secret errors; every attempted press has one release attempt."""
    adapter = Adapter(failure=failure, result=result)
    assert adapter.execute(action()) == {"ok": False, "error": "pointer_outcome_uncertain"}
    releases = [command for command in adapter.commands if command[1] == "mouseup"]
    assert len(releases) == (0 if failure == 1 else 1)
    assert len(adapter.commands) == (failure if failure in (1, 5) else failure + 1)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True])
def test_invalid_initial_clock_has_no_pointer_side_effects(bad):
    """Reject nonfinite clocks before even moving to the initial coordinate."""
    adapter = Adapter()
    adapter.now = bad
    assert adapter.execute(action())["error"] == "pointer_outcome_uncertain"
    assert adapter.commands == []


def test_late_sleep_aborts_without_bursting_overdue_points():
    """An overdue timeline is uncertain, not permission to rush all remaining moves."""
    adapter = Adapter()
    adapter.sleep = lambda seconds: setattr(adapter, "now", adapter.now + seconds + 0.5)
    assert adapter.execute(action())["error"] == "pointer_outcome_uncertain"
    assert [command[1] for command in adapter.commands] == ["mousemove", "mousedown", "mouseup"]


def test_early_sleep_and_reversed_clock_release():
    """An adapter cannot silently compress the approved timing or reverse its clock."""
    for increment in (0, -1):
        adapter = Adapter()
        adapter.sleep = lambda seconds: setattr(adapter, "now", adapter.now + increment)
        assert adapter.execute(action())["error"] == "pointer_outcome_uncertain"
        assert adapter.commands[-1] == ("xdotool", "mouseup", "1")


def test_validation_copies_without_modifying_input_and_bounds_pixels():
    """Pure validation leaves caller data intact and rejects oversized screen areas."""
    request = action()
    before = copy.deepcopy(request)
    runtime.canonical_pointer_action(request)
    assert request == before
    request.update(width=16384, height=16384)
    assert Adapter().execute(request)["error"] == "invalid_pointer_action"
