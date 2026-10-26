# Overnight runtime and cache audit — 2026-10-26

Read-only window: 2026-10-25 23:17:36 through 2026-10-26 09:35:54, UTC+8.
Start is the recorded successful release-report file time, not inferred from git time.
Code commit ecfbd6926a9c7445c6edbc353c0d154a6a3d81d4; all five manifest files match.
Evidence was collected via read-only journal/SFTP and token-scoped relay GET endpoints;
no group test messages, model requests, credential changes, configuration changes or
service restarts occurred during the audit. Subsequent cache optimization experiments
are separately identified and do make isolated synthetic model requests.

## Cache: actual-window relay records

| Metric | Value |
|---|---:|
| Requests | 241 |
| Input tokens | 2,372,374 |
| Cached input tokens | 788,352 |
| Weighted cached/input ratio | **33.2305%** |
| Output tokens | 15,350 |
| Requests with a positive cache count | 158/241 = 65.5602% |
| Cache field coverage | 241/241, including explicit zeroes |
| Input >=10,000 tokens | 137 requests; 32.1094% weighted cache |
| Cache plateaus in long prompts | 4,096 x62; 5,504 x15; 5,632 x53 |

130/137 long requests (94.89%) stop at those three cache counts. All long requests
have some cache hit, so this is predominantly low reusable-prefix coverage rather than
no cache at all. 94 short requests (211–241 input tokens) have a 7.3924% weighted rate;
these are a descriptive size cohort, not authenticated identification of a helper path.
The other 10 requests are 5,800–6,335 input tokens, with 85.1493% weighted cache.

All 241 rows share one token/model/channel and request path /v1/chat/completions. Token
scope can include helper model calls and any other process sharing that credential;
without per-request IDs in plugin logs these are not all individually attributable to
a particular conversation turn. Relay log data is upstream-reported cache usage, not a
packet-captured raw upstream response. Cache counts are complete, multiples of128 and
never greater than input. No duplicate log IDs or request IDs occurred.

The last1000 token records cover 16:05:41 Oct25 onward; 759 precede deployment. Their
55.1708% combined hit rate must NOT be presented as the overnight result. Pre-deploy
available records alone have65.2091%, but overlap different code releases, prompt sizes,
cache warmth and traffic, so they are not a controlled regression comparison.

`other.cache_ratio=0.02` is the cached-input pricing factor, NOT2% cache hit. Every `frt`
is-1000, so TTFT is unobservable from those rows. Relay duration median11s, p9517s,
maximum36s describes completed model requests only, not whole-turn response latency.
Recorded billing quota matches reconstructed pricing within integer rounding; this is
not a balance reconciliation and no billing was modified.

## Runtime and batching

- 5,591 valid journal records:5,141 INFO,8 WARN,442 multiline message continuations.
- Zero ERROR/Traceback, model-loop, selection-invalid, send or observation-archive failures
  in this journal snapshot. Syslog priority is always6; application headers were parsed.
- 1,020 received messages:740 blocked by saved admission policy;280 admitted inputs.
- 212 lifecycle batches:177 x1,22 x2,5 x3,2 x4,2 x5,3 x6,1 x8.
- 128 reply outputs +5 explicit silent completions +79 probability skips reconcile212.
- 128 persisted assistant replies reconcile those outputs after event.send returned.
  Transport message IDs were not logged: this is send-return evidence, not an independent
  recipient/OneBot receipt confirmation. One additional qq_send tool reports success.
- Approximate batch-start to answer-produced latency:median12.264s,p9016.457s,p9519.009s,
  maximum54.147s. This excludes queue/preprocessing/send completion; it is not full E2E.
- Four attempted tools persisted as successful observations:self_status,qq_send,vm_browse,
  vm_shot, across three tool-using turns. No recorded tool error in this snapshot.
- No JSON-object/fence/SILENT artifacts in persisted assistant text, and no consecutive
  identical assistant replies.11 alternating same-sender/assistant runs,longest9records,
  show continuing conversations, not proof of an autonomous bot-to-bot loop.

## Warnings and resources

Seven image-description calls exceeded25s; there was one temporary-directory cleanup
warning. Cleanup source shows this warning is emitted when storage exceeds its threshold
and files have been removed; it is not itself evidence of a failed cleanup.
AstrBot PID714988 and VM API PID714992 remain active with0 service restarts; NapCat start
time unchanged. Root disk59% used,~21GB available; memory~5,988MB available of7,939MB;
load0.66/0.54/0.57. No resource exhaustion observed at the read-only sample.

## Confirmed code issues, distinct from observed incidents

1. Each new clock is serialized before conversation/history, breaking reusable user-prefix
   content early. Persona and tool schemas are stable. Magnitude needs matched experiments.
2. Rolling last120 history drops early text frequently; this limits history-prefix reuse even
   after moving the clock. Do not promise full-context99% cache from field order alone.
3. New screenshots reconstruct the first user message instead of appending a new observation,
   breaking intra-tool-loop prefixes. Ordinary tool continuation remains append-only.
4. Usage/cache counts are discarded by the plugin; add numeric-only telemetry with unknown
   distinct from zero and never log raw bodies/keys.
5. Explicit send tools happen before final reply_to/SILENT processing, so final selection
   cannot undo them. qq_send has a fixed default group if omitted; sender/scoping safety
   warrants a separate user-approved change, not hidden inside cache optimization.
6. Global serialized model turns have no default round/whole-turn deadline by prior user
   request. An indefinitely tool-using model could delay all conversations; this was not
   observed overnight (longest answer-produced54s). No hard limit is reinstated silently.

Raw private chat and credential-bearing artifacts stay outside the public report/repository.
No production code was changed merely to complete the audit.
