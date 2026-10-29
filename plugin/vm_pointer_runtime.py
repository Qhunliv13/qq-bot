"""Deterministic pointer primitive for a future trusted VM service adapter.

This module installs no route, launches no process, and grants no authorization.
A future exclusive GUI owner must call it only after the existing visual gate
validates a ticket bound to the complete canonical action and fresh screenshot.
The injected runner/sleep/clock are trusted runtime capabilities, never model
arguments. Existing legacy drag routes remain denied; this is not deployment.
"""

import math
from collections import namedtuple


# Bounds keep a request's path, coordinate space, and scheduled duration finite.
MAX_POINTS = 128
MAX_DURATION_MS = 10000
MAX_DIMENSION = 16384
MAX_PIXELS = 16 * 1024 * 1024
# Runtime policy: abort instead of bursting overdue points; adapters must honor timeouts.
MAX_LATENESS_SECONDS = 0.250
COMMAND_TIMEOUT_SECONDS = 1.0
_Point = namedtuple("_Point", "x y t_ms")
_Action = namedtuple("_Action", "version button width height points")


def canonical_pointer_action(action):
    """Copy and validate the complete v1 request before any injected capability runs.

    Point times are scheduled offsets after button-down, not promises about OS
    latency or the cursor's physical motion between commanded positions.
    """
    if type(action) is not dict or set(action) != {"version", "button", "width", "height", "points"}:
        raise ValueError("invalid_pointer_action")
    if (type(action["version"]) is not int or action["version"] != 1
            or type(action["button"]) is not str or action["button"] != "left"):
        raise ValueError("invalid_pointer_action")
    width, height = action["width"], action["height"]
    if (type(width) is not int or type(height) is not int
            or not 1 <= width <= MAX_DIMENSION or not 1 <= height <= MAX_DIMENSION
            or width * height > MAX_PIXELS):
        raise ValueError("invalid_pointer_action")
    points = action["points"]
    if type(points) is not list or not 2 <= len(points) <= MAX_POINTS:
        raise ValueError("invalid_pointer_action")
    copied = []
    for point in points:
        # Reject bools, unknown fields, strings, and implicit backend coercions.
        if type(point) is not dict or set(point) != {"x", "y", "t_ms"}:
            raise ValueError("invalid_pointer_action")
        if any(type(point[key]) is not int for key in ("x", "y", "t_ms")):
            raise ValueError("invalid_pointer_action")
        x, y, elapsed = point["x"], point["y"], point["t_ms"]
        if (not 0 <= x < width or not 0 <= y < height
                or not 0 <= elapsed <= MAX_DURATION_MS
                or (not copied and elapsed != 0)
                or (copied and elapsed <= copied[-1].t_ms)):
            raise ValueError("invalid_pointer_action")
        copied.append(_Point(x, y, elapsed))
    return _Action(1, "left", width, height, tuple(copied))


def frozen_pointer_action_payload(action):
    """Convert only this runtime's exact immutable records; callers revalidate schema.

    Namedtuple constructors are public Python capabilities, not validation. The
    resulting detached wire payload must still pass canonical_pointer_action.
    """
    if (type(action) is not _Action or type(action.points) is not tuple
            or not 2 <= len(action.points) <= MAX_POINTS
            or any(type(point) is not _Point for point in action.points)):
        raise ValueError("invalid_pointer_action")
    return {"version": action.version, "button": action.button,
            "width": action.width, "height": action.height,
            "points": [{"x": point.x, "y": point.y, "t_ms": point.t_ms}
                       for point in action.points]}


def _checked_run(runner, argv, timeout_s=COMMAND_TIMEOUT_SECONDS):
    """Require an explicit integer zero; truthy envelopes and bools are not exit codes."""
    result = runner(argv, timeout_s=timeout_s)
    if type(result) is not int or result != 0:
        raise RuntimeError("pointer_command_failed")


def _now(clock):
    """Reject invalid injected clocks rather than authorize unbounded/NaN timing."""
    value = clock()
    if type(value) not in (int, float) or not math.isfinite(value):
        raise RuntimeError("invalid_pointer_clock")
    return value


def execute_pointer_path(action, *, runner, sleep, clock):
    """Execute copied argv points once, checking every result and releasing on uncertainty.

    The trusted runner must execute argv without a shell, synchronously wait for
    completion, and enforce the supplied timeout_s. Plain mousemove deliberately
    avoids --sync, which can stall on stationary hold points; a zero exit code is
    a command acknowledgment, not proof of physical cursor position. No retry is safe after
    an uncertain movement. A release is attempted even when button-down raises;
    this safety cleanup is not a retry of the requested action. Callers must hold
    the sole GUI execution lock across screenshot validation and this function.
    """
    try:
        canonical = canonical_pointer_action(action)
    except ValueError:
        return {"ok": False, "error": "invalid_pointer_action"}
    if not all(callable(capability) for capability in (runner, sleep, clock)):
        return {"ok": False, "error": "pointer_runtime_unavailable"}
    press_attempted = False
    succeeded = False
    released = True
    try:
        # Validate the clock before the first movement; copy is already complete.
        previous = _now(clock)
        first = canonical.points[0]
        _checked_run(runner, ("xdotool", "mousemove", str(first.x), str(first.y)))
        press_attempted = True
        _checked_run(runner, ("xdotool", "mousedown", "1"))
        started = _now(clock)
        if started < previous:
            raise RuntimeError("pointer_clock_reversed")
        previous = started
        for point in canonical.points[1:]:
            now = _now(clock)
            if now < previous:
                raise RuntimeError("pointer_clock_reversed")
            deadline = started + point.t_ms / 1000.0
            if now < deadline:
                sleep(deadline - now)
                after = _now(clock)
                # A premature sleeper cannot compress the approved hold/path timing.
                if after < deadline or after < now:
                    raise RuntimeError("pointer_sleep_incomplete")
                now = after
            if now > deadline + MAX_LATENESS_SECONDS:
                raise RuntimeError("pointer_timing_expired")
            previous = now
            remaining = deadline + MAX_LATENESS_SECONDS - now
            if remaining <= 0:
                raise RuntimeError("pointer_timing_expired")
            _checked_run(runner, ("xdotool", "mousemove", str(point.x), str(point.y)),
                         timeout_s=min(COMMAND_TIMEOUT_SECONDS, remaining))
            completed = _now(clock)
            if completed < now or completed > deadline + MAX_LATENESS_SECONDS:
                raise RuntimeError("pointer_timing_expired")
            previous = completed
        succeeded = True
    except Exception:
        # Do not leak command stderr, private paths, or injected exception strings.
        succeeded = False
    finally:
        if press_attempted:
            try:
                _checked_run(runner, ("xdotool", "mouseup", "1"))
            except Exception:
                released = False
    if not succeeded or not released:
        return {"ok": False, "error": "pointer_outcome_uncertain"}
    return {"ok": True, "version": canonical.version, "points_executed": len(canonical.points)}
