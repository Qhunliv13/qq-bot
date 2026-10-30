"""Length-aware queue partitioning using the production context serializer.

Never alter source messages or create new identities. A single irreducible input
is returned as oversized for explicit archive-only handling, not silent truncation.
"""
from __future__ import annotations

from .context_budget import limits, size
from .conversation_context import system_prompt
from .message_batch import collect_batch


def split_pending(plugin, scope, entries):
    """Return the largest chronological prefix that fits the fixed request region.

    Temporary contexts are restored even on serializer/storage failures. Current
    inputs remain complete and historical sources are selected by the normal loop.
    Binary probes use real attribution, observations, facts and tool-schema costs.
    """
    entries = list(entries)
    if not entries:
        return {"selected": [], "remaining": [], "oversized": False, "fixed_chars": 0}
    maximum = limits()["max_context_chars"]
    schemas = plugin._tool_schemas()
    system = system_prompt(getattr(plugin, "persona", "") or "")
    fixed = len(system) + size(schemas) + 4096
    contexts = getattr(plugin, "_turn_context", None)
    if contexts is None:
        plugin._turn_context = contexts = {}
    marker = object()
    previous = contexts.get(scope, marker)
    measured = {}

    def cost(count):
        """Measure exactly the current-message envelope without inherited old-turn output."""
        if count not in measured:
            batch = collect_batch(entries[:count])
            contexts[scope] = {"messages": batch["records"], "history": [],
                               "clock": plugin._clock_context(), "image_refs": []}
            prompt = plugin._request_context(batch["event"], scope, batch["text"], batch["has_image"])
            measured[count] = fixed + len(prompt)
        return measured[count]

    try:
        if cost(1) > maximum:
            return {"selected": entries[:1], "remaining": entries[1:],
                    "oversized": True, "fixed_chars": measured[1], "max_chars": maximum}
        low, high = 1, len(entries)
        while low < high:
            middle = (low + high + 1) // 2
            if cost(middle) <= maximum:
                low = middle
            else:
                high = middle - 1
        return {"selected": entries[:low], "remaining": entries[low:],
                "oversized": False, "fixed_chars": cost(low), "max_chars": maximum}
    finally:
        if previous is marker:
            contexts.pop(scope, None)
        else:
            contexts[scope] = previous
