"""Cache telemetry must distinguish true zeroes, missing fields and pricing multipliers."""
import logging

import pytest

from plugin.model_usage import log_usage, usage_metrics


def test_openai_usage_fields_report_weighted_numerator():
    """Only actual cached input tokens, not cache price ratios, measure reuse."""
    got = usage_metrics({"usage": {"prompt_tokens": 1000, "completion_tokens": 25,
                                  "prompt_tokens_details": {"cached_tokens": 750}, "cache_ratio": .02}})
    assert got["cached_tokens"] == 750
    assert got["cache_hit_percent"] == 75
    assert got["cache_source"] == "prompt_tokens_details.cached_tokens"


def test_deepseek_usage_shape_preserves_zero():
    """A reported zero differs from absent cache telemetry."""
    got = usage_metrics({"usage": {"prompt_tokens": 1000, "prompt_cache_hit_tokens": 0}})
    assert got["cached_tokens"] == 0
    assert got["cache_hit_percent"] == 0
    assert got["cache_valid"] is True


@pytest.mark.parametrize("payload", [{}, {"usage": None}, {"usage": {"cache_ratio": .02}}])
def test_unknown_usage_is_not_zero_cache(payload):
    """No numeric cache count is unknown even if a price multiplier exists."""
    got = usage_metrics(payload)
    assert got["cached_tokens"] is None
    assert got["cache_hit_percent"] is None
    assert got["cache_valid"] is False


@pytest.mark.parametrize("value", [True, -1, 1.5, "100", float("inf")])
def test_invalid_counts_do_not_make_a_hit(value):
    """Reject malformed numeric fields rather than silently changing denominator semantics."""
    assert usage_metrics({"usage": {"cached_tokens": value}})["cached_tokens"] is None


def test_impossible_cached_count_is_explicitly_invalid():
    """A provider inconsistency is visible, not clamped into a fictitious 100 percent."""
    got = usage_metrics({"usage": {"prompt_tokens": 10, "cached_tokens": 20}})
    assert got["cached_tokens"] == 20
    assert got["cache_valid"] is False
    assert got["cache_hit_percent"] is None


def test_logger_never_emits_body_credentials_or_ids(caplog):
    """The telemetry allowlist contains counts only, never raw responses or user text."""
    response = {"secret": "secret-to-exclude", "id": "private-request-id", "choices": [{"text": "private-user-answer"}],
                "usage": {"prompt_tokens": 100, "cached_tokens": 25}}
    with caplog.at_level(logging.INFO):
        log_usage(logging.getLogger(__name__), response, transport="tools")
    assert "cached_tokens" in caplog.text
    for forbidden in ("secret-to-exclude", "private-request-id", "private-user-answer"):
        assert forbidden not in caplog.text
