# Lossless cache-prefix locality (offline evidence)

## Scope and actual change

Baseline: `f06174b`. In [conversation_context.py](../plugin/conversation_context.py),
move the existing optional `conversation.history_context` field from after current
`scope`/`messages` to immediately after `history`:

```text
before: history, scope, messages, history_context, anchor
 after: history, history_context, scope, messages, anchor
```

The shared timeline metadata contains mode, source and exact coverage. When shared
history/coverage are unchanged but the current batch or scope changes, this small
metadata block can now remain in the prefix. No field is added or removed; absent
history metadata preserves the old wire bytes. The schema/version is unchanged.
The top-level observation-first layout, full history and chronological ordering,
all nested insertion order, exact numeric/time values, Unicode text, IDs, source
labels and media references are unchanged. No truncation, summarization, rounding,
selection, deduplication, tool sorting or provider-specific cache controls are added.
No production configuration, model, temperature or feature defaults are changed.

## Reproducible comparison

Run locally, without credentials, network, production data or provider requests:

```powershell
python tests/test_lossless_cache_prefix.py
python -m pytest tests/test_lossless_cache_prefix.py tests/test_conversation_context.py tests/test_observation_prefix_layout.py tests/test_prefix_metrics.py tests/test_persisted_memory_integration.py -q
```

[test_lossless_cache_prefix.py](../tests/test_lossless_cache_prefix.py) reconstructs
the complete baseline top-level and conversation key ordering, rejects duplicate
keys when parsing, asserts identical parsed objects and exact total byte lengths,
and asserts inputs remain unmodified. It exercises the actual native image assembler
and proves that only the envelope string's object-member order changes: image parts,
provenance labels, tools (including their deliberately non-alphabetical order),
system prompt, model and temperature remain identical. The fixture has 240 synthetic
history records and exact microsecond timestamps; no real chats are retained.

Exact common UTF-8 byte prefixes of two successive envelopes:

| Synthetic change | Before | After | Delta |
|---|---:|---:|---:|
| Current message + exact clock | 107,378 | 107,707 | +329 |
| History append + current/clock | 107,331 | 107,331 | 0 |
| Image + current/clock | 107,378 | 107,707 | +329 |
| Memory edit + current/clock | 107,378 | 107,707 | +329 |
| Observation append + current/clock | 853 | 853 | 0 |
| Scope switch (including scoped memory) + current/clock | 107,342 | 107,671 | +329 |
| Coverage + current/clock | 107,378 | 107,522 | +144 |
| Coverage only, unchanged current batch | 107,616 | 107,522 | **−94** |

For the +329 envelope cases, serialized full synthetic request JSON gains **373**
common-prefix bytes because its embedded envelope quotes are escaped. These request
JSON bytes are a local encoding, **not a claim about provider tokenization or wire
serialization**. The exact equal-weight synthetic sum is 752,654 → 754,020 bytes
(+1,366); this is not a traffic-weighted production improvement forecast.

History append already differs inside history, before this metadata: zero extra
benefit is expected and tested. Metadata-only changes can lose the prefix portion
formerly occupied by scope/current (−94 in this fixture), but never sacrifice any
history bytes. A very large unchanged current batch can make this downside larger;
no universal improvement or cache-hit guarantee is asserted. Existing prefix
telemetry remains the unchanged privacy-safe 256-byte digest lower-bound metric.
These exact offline comparisons must not be relabeled provider cached tokens,
latency savings, cost savings or a production hit rate.

## Rejected larger reorder and inspected boundaries

Moving persisted `memory` before the conversation looked more profitable for a
same-scope request but is **not shipped**. The actual
[fact_context.py](../plugin/fact_context.py) memory projection carries the current
scope and scoped entries; memory is not a globally stable prefix. The executable
comparator retains this rejected experiment with identical parsed semantics:

| Rejected memory-first case | Prefix gain |
|---|---:|
| Same-scope current change | +5,650 |
| History append | +5,650 |
| Scoped memory changes during scope switch | **−100,868** |
| Memory text edit | **−106,479** |

Thus two gains would not compensate for even one scope switch in this fixture.
Do not move scoped memory before the shared history on an assumption of stability.

[image_transcript.py](../plugin/image_transcript.py) already places initial image
parts **after** the envelope text and appends later tool images without rewriting
submitted messages. It was inspected, not changed. The shared-history aligned
240 + 0..29 window already exists and is untouched. History's rendered `line` and
native `record` can carry overlapping text but distinct provenance/representation;
neither is removed as a supposed duplicate. The observation-first layout has
known scope/observation-change tradeoffs and is deliberately not reversed here.

This patch is deliberately modest: lossless metadata locality rather than an
unsupported claim of substantially improved production cache hits. No deployment
or production/network verification was performed.
