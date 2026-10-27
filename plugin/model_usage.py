"""Numeric-only provider usage telemetry without credentials or conversation content."""
import math


def _count(value):
    """Preserve absent/invalid usage as unknown, never fabricate a zero cache hit."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if math.isfinite(value) and value >= 0 and int(value) == value else None


def usage_metrics(response):
    """Normalize known usage fields while retaining which cache field reported them."""
    response = response if isinstance(response, dict) else {}
    usage = response.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    details = usage.get("prompt_tokens_details")
    details = details if isinstance(details, dict) else {}
    prompt = _count(usage.get("prompt_tokens"))
    completion = _count(usage.get("completion_tokens"))
    candidates = [("prompt_tokens_details.cached_tokens", details.get("cached_tokens")),
                  ("prompt_cache_hit_tokens", usage.get("prompt_cache_hit_tokens")),
                  ("cached_tokens", usage.get("cached_tokens"))]
    cached, source = None, "unknown"
    for field, value in candidates:
        valid = _count(value)
        if valid is not None:
            cached, source = valid, field
            break
    valid_cache = cached is not None and (prompt is None or cached <= prompt)
    return {"prompt_tokens": prompt, "completion_tokens": completion,
            "cached_tokens": cached, "cache_source": source,
            "cache_valid": valid_cache,
            "cache_hit_percent": 100 * cached / prompt if valid_cache and prompt else None}


def log_usage(logger, response, *, transport, request_index=None):
    """Log a fixed allowlist of numeric metrics; raw responses and IDs stay private."""
    import json
    metrics = usage_metrics(response)
    # The index is caller-owned numeric telemetry, not a provider/user identifier.
    # Keep malformed or absent indexes out rather than guessing correlation.
    if type(request_index) is int and request_index > 0:
        metrics["request_index"] = request_index
    logger.info("[higan] model_usage transport=%s metrics=%s", transport,
                json.dumps(metrics, ensure_ascii=False, separators=(",", ":")))
    return metrics
