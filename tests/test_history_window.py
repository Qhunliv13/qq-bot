"""Offline invariants for stable source prefixes without message batching."""

import json
from pathlib import Path
import sys

import pytest

# Import the pure namespace without loading the framework-dependent main plugin.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugin.conversation_context import build_context  # noqa: E402
from plugin.history_window import append_history, history_snapshot  # noqa: E402
from plugin.context_budget import size  # noqa: E402


@pytest.mark.parametrize("size", [0, 1, 119, 120, 121, 200, 239, 240, 241, 359, 360])
def test_snapshot_preserves_latest_entries_order_and_window_bound(size):
    """Every newest source entry survives unchanged and surplus is window-derived."""
    history = [f"entry-{number}" for number in range(size)]
    before = history.copy()
    frozen = history_snapshot(history, 0)
    assert history == before
    assert isinstance(frozen, tuple)
    assert frozen[-120:] == tuple(history[-120:])
    assert frozen == tuple(history[size - len(frozen):])
    assert len(frozen) == (size if size < 120 else 120 + size % 120)
    assert len(frozen) <= 239


def test_initial_restored_200_and_block_rollover():
    """The existing restore remains intact until the next derived block boundary."""
    restored = [f"restored-{number}" for number in range(200)]
    first = history_snapshot(restored, 0)
    assert first == tuple(restored)
    restored.extend(f"new-{number}" for number in range(39))
    before_roll = history_snapshot(restored, 0)
    assert before_roll[:len(first)] == first
    assert len(before_roll) == 239
    restored.append("boundary")
    after_roll = history_snapshot(restored, 0)
    assert after_roll == tuple(restored[-120:])
    assert after_roll[0] == "restored-120"


def test_long_running_capped_history_keeps_stable_prefix_and_all_latest():
    """Absolute origins preserve blocks after repeated evictions past 4000 entries."""
    histories, origins = {}, {}
    previous, previous_start = (), None
    for number in range(6500):
        append_history(histories, origins, "scope", f"entry-{number}", capacity=2000)
        history = histories["scope"]
        frozen = history_snapshot(history, origins["scope"])
        expected = tuple(f"entry-{index}" for index in range(max(0, number - 119), number + 1))
        assert frozen[-120:] == expected
        assert len(frozen) <= 239
        assert frozen == tuple(history[-len(frozen):])
        assert len(history) == min(number + 1, 2000)
        assert origins["scope"] == max(0, number + 1 - 2000)
        start = number + 1 - len(frozen)
        if previous_start == start:
            assert frozen[:len(previous)] == previous
        elif previous_start is not None:
            assert start - previous_start == 120
        previous, previous_start = frozen, start
    assert origins["scope"] > 4000


def test_scopes_reply_append_and_frozen_current_separation():
    """Received and confirmed-sent entries share chronology without scope bleed."""
    histories = {"group": [f"prior-{number}" for number in range(120)], "private": ["secret"]}
    origins = {}
    prior = history_snapshot(histories["group"], origins.get("group", 0))
    append_history(histories, origins, "group", "member：current")
    append_history(histories, origins, "group", "彼岸酱：confirmed reply")
    append_history(histories, origins, "group", "【群动态】joined")
    following = history_snapshot(histories["group"], origins["group"])
    assert prior == tuple(f"prior-{number}" for number in range(120))
    assert following == prior + ("member：current", "彼岸酱：confirmed reply", "【群动态】joined")
    assert "member：current" not in prior
    assert histories["private"] == ["secret"]
    assert "private" not in origins
    assert "secret" not in following


def test_source_text_duplicate_lines_and_snapshot_immutability():
    """No textual deduplication, normalization, shortening, or aliasing is allowed."""
    histories, origins = {}, {}
    line = "  unchanged\n雪🙂" + "x" * 5000
    append_history(histories, origins, "scope", line)
    append_history(histories, origins, "scope", line)
    frozen = history_snapshot(histories["scope"], origins["scope"])
    append_history(histories, origins, "scope", "later")
    assert frozen == (line, line)
    assert histories["scope"] == [line, line, "later"]


def test_capacity_and_minimum_are_existing_budget_parameters():
    """The same generic calculation derives its block size from caller semantics."""
    histories, origins = {}, {}
    for number in range(41):
        append_history(histories, origins, "scope", str(number), capacity=17)
        frozen = history_snapshot(histories["scope"], origins["scope"], minimum=5)
        assert frozen[-5:] == tuple(str(index) for index in range(max(0, number - 4), number + 1))
        assert len(frozen) <= 9
    assert origins["scope"] == 24
    assert len(histories["scope"]) == 17


def test_logical_origin_survives_in_process_handoff():
    """Copying origins with recent histories does not restart cache block phases."""
    histories, origins = {"scope": [str(number) for number in range(2000)]}, {"scope": 2333}
    before = history_snapshot(histories["scope"], origins["scope"])
    replacement, next_origins = histories.copy(), origins.copy()
    append_history(replacement, next_origins, "scope", "confirmed reply", capacity=2000)
    after = history_snapshot(replacement["scope"], next_origins["scope"])
    assert after[:len(before)] == before
    assert next_origins["scope"] == 2334


def test_serialized_history_prefix_survives_fresh_clock_and_current_input():
    """The unchanged history remains a real common byte prefix, not a frozen clock."""
    history = [f"entry-{number}" for number in range(200)]
    first = build_context(scope="42", history=history_snapshot(history, 0),
                          messages=[{"body": "first"}], clock={"now": "first"})
    history.append("member：first")
    history.append("彼岸酱：answer")
    second = build_context(scope="42", history=history_snapshot(history, 0),
                           messages=[{"body": "second"}], clock={"now": "second"})
    before, after = json.loads(first), json.loads(second)
    # Remove known structural list/object closings, not text markers that can
    # occur inside raw history. Appended chronological rows follow these bytes.
    assert before["observations"] == after["observations"] == []
    stable = {"version": before["version"], "source": before["source"],
              "observations": before["observations"],
              "conversation": {"history": before["conversation"]["history"]}}
    prefix = json.dumps(stable, ensure_ascii=False, separators=(",", ":"))[:-3].encode("utf-8")
    assert first.encode("utf-8").startswith(prefix)
    assert second.encode("utf-8").startswith(prefix)
    assert before["source"] == after["source"]
    assert before["conversation"]["scope"] == after["conversation"]["scope"] == "42"
    assert before["timing"] == {"now": "first"} and after["timing"] == {"now": "second"}
    assert before["conversation"]["messages"] == [{"body": "first"}]
    assert after["conversation"]["messages"] == [{"body": "second"}]
    assert after["conversation"]["history"] == before["conversation"]["history"] + [
        "member：first", "彼岸酱：answer",
    ]


@pytest.mark.parametrize("minimum", [0, -1])
def test_invalid_minimum_is_explicit(minimum):
    """An invalid caller window must not silently delete or widen source history."""
    with pytest.raises(ValueError, match="minimum"):
        history_snapshot(["source"], 0, minimum=minimum)


def test_invalid_origin_and_capacity_are_explicit():
    """Invalid bookkeeping fails before mutating caller-owned history state."""
    histories, origins = {"scope": ["source"]}, {}
    with pytest.raises(ValueError, match="origin"):
        history_snapshot(histories["scope"], -1)
    with pytest.raises(ValueError, match="capacity"):
        append_history(histories, origins, "scope", "new", capacity=0)
    assert histories == {"scope": ["source"]}
    assert origins == {}


def test_default_append_has_no_message_count_eviction(monkeypatch):
    """Thousands of tiny entries survive below the configured serialized budget."""
    monkeypatch.setattr("plugin.history_window.limits", lambda: {"max_context_chars": 600000})
    histories, origins = {}, {}
    for index in range(2501):
        append_history(histories, origins, "scope", str(index))
    assert histories["scope"] == [str(index) for index in range(2501)]
    assert origins == {"scope": 0}
    assert len(history_snapshot(histories["scope"], 0, max_chars=600000)) == 2501


def test_default_append_uses_configured_length_not_count(monkeypatch):
    """Only a few large entries can exhaust the configured cache length."""
    line = "  雪🙂\n" + "x" * 3000
    maximum = size([line])
    monkeypatch.setattr("plugin.history_window.limits", lambda: {"max_context_chars": maximum})
    histories, origins = {}, {}
    append_history(histories, origins, "scope", line)
    append_history(histories, origins, "scope", line)
    assert histories == {"scope": [line]}
    assert origins == {"scope": 1}


def test_length_snapshot_exact_json_whitespace_and_no_mutation():
    """Unicode, escaping and significant spaces count exactly without normalization."""
    history = ["older", "  雪🙂\n\"quoted\"\\  "]
    maximum = len(json.dumps(history[-1:], ensure_ascii=False, separators=(",", ":")))
    assert size(history[-1:]) == maximum
    assert history_snapshot(history, 9000, max_chars=maximum) == tuple(history[-1:])
    assert history_snapshot(history, 0, max_chars=maximum - 1) == ()
    assert history == ["older", "  雪🙂\n\"quoted\"\\  "]


def test_explicit_length_overrides_legacy_cache_capacity():
    """An explicit runtime length budget cannot be narrowed by an old row quota."""
    histories, origins = {}, {}
    for index in range(500):
        append_history(histories, origins, "scope", str(index), capacity=1, max_chars=10000)
    assert len(histories["scope"]) == 500
    assert origins["scope"] == 0
    assert history_snapshot(histories["scope"], 0, minimum=1, max_chars=10000) == tuple(
        str(index) for index in range(500))


@pytest.mark.parametrize("maximum", [-1, True, 1.5, "100"])
def test_invalid_length_fails_before_cache_mutation(maximum):
    """Invalid size policy must not append or evict caller-owned source evidence."""
    histories, origins = {"scope": ["source"]}, {}
    with pytest.raises(ValueError, match="max_chars"):
        append_history(histories, origins, "scope", "new", max_chars=maximum)
    with pytest.raises(ValueError, match="max_chars"):
        history_snapshot(histories["scope"], 0, max_chars=maximum)
    assert histories == {"scope": ["source"]}
    assert origins == {}
