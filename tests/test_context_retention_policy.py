"""Pure serialized-length retention contracts; no runtime, vendor or network imports."""

from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugin.context_budget import allocation, limits, size, suffix  # noqa: E402
from plugin.history import Recorder  # noqa: E402


@pytest.mark.parametrize("maximum,fixed,overhead", [
    (600000, 10000, 2048), (60000, 17000, 2048),
    (10, 0, 0), (11, 0, 0), (12, 0, 0), (13, 0, 0), (14, 0, 0),
    (4096, 2048, 2048), (4096, 4097, 2048), (0, 0, 0),
])
def test_allocation_splits_remaining_characters_without_losing_rounding(maximum, fixed, overhead):
    """Fixed costs are removed first; rounding residue belongs to the summary region."""
    result = allocation(maximum, fixed, overhead_chars=overhead)
    available = max(0, maximum - fixed - overhead)
    assert result == {
        "available_chars": available, "raw_chars": available * 3 // 5,
        "summary_chars": available - available * 3 // 5,
        "raw_fraction": 0.6, "summary_archive_fraction": 0.4,
        "overhead_chars": overhead,
    }
    assert result["raw_chars"] + result["summary_chars"] == available


@pytest.mark.parametrize("field", ["maximum", "fixed_chars", "overhead_chars"])
@pytest.mark.parametrize("invalid", [-1, True, False, 1.5, "4096", None])
def test_allocation_rejects_noninteger_or_negative_costs(field, invalid):
    """Booleans and coercible strings cannot silently alter a capacity policy."""
    arguments = {"maximum": 60000, "fixed_chars": 1000, "overhead_chars": 2048}
    arguments[field] = invalid
    with pytest.raises(ValueError, match="nonnegative integers"):
        allocation(**arguments)


@pytest.mark.parametrize("maximum", [4096, 4097, 600000])
def test_limits_reserves_two_fifths_for_summary(monkeypatch, maximum):
    """The public limit no longer retains the deliberately replaced one-eighth default."""
    monkeypatch.setattr("plugin.runtime_settings.load_settings", lambda: {
        "context_memory": {"max_context_chars": maximum}})
    assert limits() == {"max_context_chars": maximum, "summary_chars": maximum * 2 // 5}


@pytest.mark.parametrize("invalid", [4095, -1, True, 6000.0, "6000", None])
def test_limits_rejects_invalid_capacity(monkeypatch, invalid):
    """Capacity validation remains fail-closed rather than coercing configuration values."""
    monkeypatch.setattr("plugin.runtime_settings.load_settings", lambda: {
        "context_memory": {"max_context_chars": invalid}})
    with pytest.raises(ValueError):
        limits()


@pytest.mark.parametrize("budget", [0, 1, 2, 10])
def test_oversized_latest_is_archive_only_without_source_mutation(budget):
    """An unfittable whole newest record is excluded explicitly, never clipped or deleted."""
    rows = [{"id": "newest", "body": "x" * 100}]
    before = deepcopy(rows)
    selected, coverage = suffix(rows, budget)
    assert selected == [] and rows == before
    assert coverage["oversized_latest"] is True
    assert coverage["source_chars"] == coverage["omitted_source_chars"] == size(rows[0])
    assert coverage["retained_source_chars"] == 0
    assert coverage["retained_source_fraction"] == 0
    assert coverage["archive_deleted_records"] == 0


@pytest.mark.parametrize("invalid", [-1, True, 1.5, "20", None])
def test_suffix_rejects_invalid_budget(invalid):
    """Whole-record selection uses the same strict integer units as allocation."""
    with pytest.raises(ValueError):
        suffix([], invalid)


def test_empty_source_has_complete_coverage():
    """An empty source has no omitted characters, including no array-punctuation loss."""
    selected, coverage = suffix([], 2)
    assert selected == []
    assert coverage["used_chars"] == 2
    assert coverage["source_chars"] == coverage["retained_source_chars"] == coverage["omitted_source_chars"] == 0
    assert coverage["retained_source_fraction"] == 1.0
    assert coverage["archive_deleted_records"] == 0


def test_length_eviction_is_not_a_sixty_percent_message_quota_and_archive_survives(tmp_path):
    """Many short recent rows fit after one long source leaves the raw region unchanged on disk."""
    recorder = Recorder(str(tmp_path / "history"))
    records = [{"id": "long", "body": "旧讨论" * 3000}] + [
        {"id": str(index), "body": 'short "quoted" \\ text ' + str(index)} for index in range(30)]
    for record in records:
        recorder.append("42", record["body"], record=record)
    sources = recorder.load_records("42", limit=None)
    before = deepcopy(sources)
    maximum = size(sources[1:]) * 5 // 3 + 10
    region = allocation(maximum, 0, overhead_chars=0)
    selected, coverage = suffix(sources, region["raw_chars"])
    assert selected == sources[1:]
    assert len(selected) / len(sources) > 0.9, "60% is a character allowance, not 60% of records"
    assert 0 < size(selected) <= region["raw_chars"]
    assert size([sources[0], *selected]) > region["raw_chars"]
    assert coverage["source_chars"] == sum(size(row) for row in sources)
    assert coverage["retained_source_chars"] == sum(size(row) for row in selected)
    assert coverage["omitted_source_chars"] == size(sources[0])
    assert coverage["retained_source_fraction"] == pytest.approx(
        coverage["retained_source_chars"] / coverage["source_chars"])
    assert 0 < coverage["retained_source_fraction"] < 0.6
    assert coverage["archive_deleted_records"] == 0
    assert recorder.load_records("42", limit=None) == before
    assert len(Recorder(str(tmp_path / "history")).load_records("42", limit=None)) == len(records)
    selected[0]["record"]["body"] = "derived mutation"
    assert sources == before, "The selected view must be detached from original evidence"
