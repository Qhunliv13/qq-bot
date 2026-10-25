"""Offline autonomy regressions plus explicitly labeled remaining bug observations.

Reuse the established unchanged-production AST fixture. All providers, sends,
configuration reads, random samples and clock ticks used here are local doubles.
"""

import ast
import asyncio
import copy
import io
import json
from types import SimpleNamespace

import pytest

from test_production_integration import At, Event, ROOT

pytest_plugins = ("test_production_integration",)


def _extra_method(plugin, name):
    """Compile one additional production method unchanged, without plugin imports."""
    path = ROOT / "plugin" / "main.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    klass = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                 and node.name == "HiganPlugin")
    method = copy.deepcopy(next(node for node in klass.body
                                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                                and node.name == name))
    method.decorator_list = []
    namespace = dict(type(plugin).on_message.__globals__)
    module = ast.Module(body=[ast.ImportFrom(module="__future__",
                        names=[ast.alias(name="annotations")], level=0), method], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace[name], namespace


async def _empty_forward(event):
    """Avoid unrelated forward expansion and all transport effects."""
    return ""


def _entry(plugin, tmp_path, pct):
    """Keep actual admission/handle/send while replacing only unrelated edges."""
    (tmp_path / "groups.json").write_text(json.dumps({"reply_pct": {"42": pct}}), encoding="utf-8")
    plugin._forward_to_text = _empty_forward
    plugin._wake_hit = lambda *args: ""
    plugin._rate_ok = lambda *args: True


@pytest.mark.parametrize("draws,expected", [([0.1], 1), ([0.49], 1),
                                            ([0.9], 0), ([0.6], 0)])
def test_probability_is_sampled_once_for_collected_turn(production, tmp_path, monkeypatch, draws, expected):
    """A normal 50-percent message passes exactly one gate, never the former p-squared gate."""
    monkeypatch.setenv("HIGAN_BATCH_WINDOW", "0")
    plugin, event = production(), Event("ordinary message")
    _entry(plugin, tmp_path, 50)
    samples = iter(draws)
    used = []

    def random_sample():
        """Fail on unexpected extra probability decisions and record each sample."""
        sample = next(samples)
        used.append(sample)
        return sample

    async def decide(*args):
        """Only the model boundary is replaced; actual send remains in use."""
        return "locally admitted"

    monkeypatch.setattr(type(plugin).on_message.__globals__["random"], "random", random_sample)
    plugin._decide_tools_first = decide
    asyncio.run(plugin.on_message(event))
    assert len(event.sent) == expected
    assert used == draws


def test_custom_wake_is_preserved_through_batch_handle(production, tmp_path, monkeypatch):
    """Custom wake remains eligible through collection and bypasses zero reply probability."""
    monkeypatch.setenv("HIGAN_BATCH_WINDOW", "0")
    plugin, event = production(), Event("custom wake word")
    _entry(plugin, tmp_path, 0)
    plugin._wake_hit = lambda *args: "word"
    samples = []
    decisions = []

    def random_sample():
        """Record an erroneous random sample; eligible wakes should never need one."""
        samples.append(0.5)
        return 0.5

    async def decide(*args):
        """Record whether the model boundary is ever reached."""
        decisions.append(True)
        return "wake preserved"

    monkeypatch.setattr(type(plugin).on_message.__globals__["random"], "random", random_sample)
    plugin._decide_tools_first = decide
    assert not plugin._is_woken(event)
    asyncio.run(plugin.on_message(event))
    assert samples == [] and decisions == [True] and len(event.sent) == 1


def test_person_miss_cannot_cancel_actual_at_batch_wake(production, tmp_path, monkeypatch):
    """Actual self mention preserves eligibility even when per-person probability misses."""
    monkeypatch.setenv("HIGAN_BATCH_WINDOW", "0")
    plugin, event = production(), Event("actual self mention", components=[At(qq="90001")])
    event.is_at_or_wake_command = True
    _entry(plugin, tmp_path, 0)
    plugin._wake_hit = lambda *args: "person"
    plugin._person_conf = lambda qq: {"pct": 0, "words": [], "hours": []}
    plugin._hours_ok = lambda hours: True
    monkeypatch.setattr(type(plugin).on_message.__globals__["random"], "random", lambda: 0.5)
    handled = []

    async def handle(*args):
        """Observe entry dispatch without replacing the actual admission logic."""
        handled.append(args)

    plugin._handle_one = handle
    assert plugin._directly_mentioned(event) and plugin._is_woken(event)
    asyncio.run(plugin.on_message(event))
    assert len(handled) == 1 and handled[0][-1] is True
    assert event.sent == [] and event.stopped


def test_observed_initialize_does_not_start_free_tick(production):
    """Boundary: only explicit tasks, polling and member watching start at init."""
    plugin = production()
    initialize, _ = _extra_method(plugin, "initialize")
    plugin.manifest = []
    plugin._ready = asyncio.Event()
    started = []

    async def drain():
        """No previous instance exists in this offline fixture."""
        return None

    async def watch():
        """Represent the member watcher without starting a background task."""
        return None

    async def poll():
        """Represent the polling loop without touching configuration or transport."""
        return None

    def start(coroutine):
        """Observe scheduled coroutine identities and close every unstarted object."""
        started.append(coroutine.cr_code.co_name)
        coroutine.close()
        return SimpleNamespace(done=lambda: False)

    plugin._drain_previous_instances = drain
    plugin._watch_members, plugin._poll_loop = watch, poll
    plugin._start_background = start
    asyncio.run(initialize(plugin))
    assert started == ["watch", "poll", "_task_loop"]
    assert "_free_tick" not in started and plugin._ready.is_set()


def test_observed_bug_wake_at_same_time_next_day_never_refires(production):
    """BUG: date-free group@HH:MM dedup treats next day's slot as already fired."""
    plugin = production()
    poll, namespace = _extra_method(plugin, "_poll_loop")
    plugin._recent = {"42": ["harmless local history"]}
    dates = iter(["2026-10-25", "2026-10-26"])
    sampled_days, sent = [], []
    config = json.dumps({"wake_at": {"42": ["09:00"]}})

    async def sleep(seconds):
        """Sample two different simulated days, then stop the otherwise endless loop."""
        try:
            sampled_days.append(next(dates))
        except StopIteration:
            raise asyncio.CancelledError

    def clock_format(pattern):
        """Both simulated days are the configured local wall-clock minute."""
        assert pattern == "%H:%M"
        return "09:00"

    def local_open(path, *args, **kwargs):
        """Only the expected policy read is available; never open real host config."""
        assert path == "/root/higan/groups.json"
        return io.StringIO(config)

    async def model(*args):
        """Return a deterministic visible choice without using any provider."""
        return "local scheduled message"

    async def send(*args):
        """Record the attempted proactive delivery, never contact a transport."""
        sent.append(args)
        return {"ok": True}

    namespace["asyncio"] = SimpleNamespace(sleep=sleep, CancelledError=asyncio.CancelledError)
    namespace["time"] = SimpleNamespace(strftime=clock_format)
    namespace["open"] = local_open
    plugin._llm_plain, plugin._qq_send = model, send
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(poll(plugin))
    assert sampled_days == ["2026-10-25", "2026-10-26"]
    assert len(sent) == 1 and plugin._wake_at_fired == {"42@09:00": True}
