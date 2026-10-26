"""Honor the selected provider's explicit transport deadline without arbitrary clamps."""
import math


def request_timeout(source, default):
    """Use a valid configured HTTP timeout, falling back only when absent/invalid.

    Zero is an explicit unbounded request, matching asyncio.wait_for/urlopen None;
    this does not add whole-turn retries or change the model's decision budget.
    """
    raw = source.get("timeout") if isinstance(source, dict) else None
    if raw is None or isinstance(raw, bool):
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value) or value < 0:
        return default
    return value if value > 0 else None
