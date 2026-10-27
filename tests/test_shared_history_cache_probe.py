"""Synthetic prefix controls never contact a provider unless explicitly opted in."""
import importlib.util
from pathlib import Path
import urllib.request


def _probe():
    """Load only the independent diagnostic, never the AstrBot runtime."""
    path = Path(__file__).resolve().parents[1] / "tools" / "shared_history_cache_probe.py"
    spec = importlib.util.spec_from_file_location("test_shared_history_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_offline_preserves_latest_and_metadata(monkeypatch):
    """Real archive readers preserve all native fields and need no HTTP connection."""
    def forbidden(*args, **kwargs):
        """Fail immediately if an offline diagnostic attempts HTTP."""
        raise AssertionError("offline provider call")
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    report = _probe().run_probe()
    assert report["provider_calls"] == 0
    assert report["production_history_reads"] == 0
    assert report["context_limit_declared"] is None
    assert report["provider_cache_effect"] == "unmeasured"
    for case in report["cases"].values():
        assert case["latest_240_preserved"]
        assert case["native_metadata_preserved"]
        assert case["chronological"]
        assert case["max_added_older_rows"] <= 29


def test_alignment_improves_exact_prefix_without_dropping_recent_rows():
    """500→501 within a 30-row epoch shares a head unlike rolling 240 rows."""
    probe = _probe()
    cases = probe.build_cases()
    before = cases["before"]["aligned"]["records"]
    after = cases["after"]["aligned"]["records"]
    assert len(before) == 260
    assert len(after) == 261
    assert before == after[:-1]
    assert before[-240:] == cases["before"]["rolling"]["records"]
    assert after[-240:] == cases["after"]["rolling"]["records"]
    report = probe.run_probe()
    assert report["prefix_gain_bytes"] > 10000


def test_wire_is_lossless_unicode_json():
    """Nested quotes, literal newlines and source metadata survive one serialization."""
    import json
    probe = _probe()
    rows = probe.build_cases()["after"]["aligned"]["records"]
    decoded = json.loads(probe._wire(rows))
    assert decoded["conversation"]["history"] == rows
    assert "observations" in decoded
    assert decoded["observations"] == []
