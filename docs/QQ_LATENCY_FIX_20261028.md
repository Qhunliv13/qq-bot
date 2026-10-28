# QQ latency fix, 2026-10-28

## Observed before fix

Thirty-nine model requests could be paired from existing prefix/usage timestamps:
median approximately 30.9 seconds, maximum 121.2 seconds. These are model transport
intervals, not controlled end-to-end reply measurements. Native sends observed in the
same logs were generally about 0.4–2 seconds. Resources were not saturated.

The live busiest scope had 100 retained tool observations. `_request_context` sent the
entire archive via `recent()`, bypassing the archive's existing `max_prompt_chars`
selection. That observation section measured 188735 UTF-8 bytes. The same records,
projected through the existing configured prompt budget, measured 18401 bytes with
12 retained for automatic context and 88 omitted from that request.

## Change

Reuse `ToolContextArchive.prompt()` for automatically attached previous observations.
This is **context selection**, not lossless compression of the entire archive. Omitted
count, selected count, budget and detail tool are explicit metadata. No archive,
conversation memory, persona, tool schema, model configuration or history retention is
removed. The original 100 observations remain stored. `tool_observations` now accepts
an optional nonnegative `offset` so every retained observation can be queried, not just
the newest 20. Default query behavior is preserved.

Current-turn tool results and their native call/reply pairing stay complete. The shared
FIFO and unknown-mutation/no-replay rules are unchanged. We do not force model silence,
remove tools, shorten replies or cancel an active model turn to simulate responsiveness.
A numeric `model_latency` log makes actual provider time/request bytes measurable without
logging prompts, scope IDs or credentials.

## Limits

This corrects a verified payload inflation bug; it does not guarantee a fixed reply
time. Shared public history remains available in full under its existing window.
Upstream/model time, images, additional tools and FIFO contention can still be slow.
The approximately 90% reduction applies to the observation section of one observed
scope, not to total request tokens or end-to-end response latency.
