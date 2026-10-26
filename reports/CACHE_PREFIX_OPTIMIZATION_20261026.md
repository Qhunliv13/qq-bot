# Cache-prefix optimization — 2026-10-26

## Baseline and purpose

Last deployment's read-only window (Oct25 23:17:36–Oct26 09:35:54 UTC+8) contains241
relay token-scoped completed requests with2,372,374 input and788,352 cached tokens:
33.2305% weighted cache. Long prompts>=10k have32.1094%. Full audit is in
OVERNIGHT_CACHE_AUDIT_20261026.md. These are actual relay cache-token counts, not the
0.02 cached-input pricing factor or a mean of per-request percentages.

## Changes without deleting context

- Move fresh exact timing to the END of the JSON envelope. The final conservative
  candidate preserves conversation before observations in the original source order.
  An earlier observations-first candidate was rolled back after native tool verification
  timed out repeatedly while previous-source control passed. Field values, source trust, persona, message order,
  scope, history, tasks and attachments round-trip unchanged. No frozen or rounded clock,
  no compressed/deleted chat, no cache-specific canned answer or synthetic hit counts.
- Freeze initial multipart images before the first model call. Each new screenshot batch
  becomes a separate data observation appended after native tool-result pairs in the SAME
  transcript. Previously submitted user messages and image URLs are never rewritten.
  A new screenshot acquisition at an identical path is converted again, so fresh image
  content is not lost; the older submitted data URL remains immutable.
- Add one numeric-only model_usage module reused by plain/chat/tool calls. It records
  input/output/cached tokens, reported field and valid hit fraction. Missing usage stays
  unknown, not0; cache>input stays explicitly invalid, not clamped. No raw response,
  question, credential or request ID is logged by the new telemetry.

Existing user policies, lifecycle batches, positive reply selection, tool contracts and
provider max_tokens settings are not changed by this release. No model round/time limit
is reinstated. Image count per new batch follows existing transport constraints; the
whole transcript is not silently truncated to the last3 images.

## Matched synthetic cache comparison

Same actual provider/model/persona/tool schemas and semantic fields; old-vs-new JSON order,
same prompt lengths,120-record sliding synthetic histories, no tools executed and no QQ
sends. Alternate variant order; caches NOT flushed and upstream routing NOT controlled.
No production chat used. Each arm3 requests; a timed-out request without usage is retained
as a missing sample, not counted as zero or removed from the request count.

First repeated-prose experiment was INCONCLUSIVE: both variants reached99.52% warm cache,
and the optimized arm had one missing-usage timeout. It is NOT evidence of gain.

Second experiment uses independently hashed record contents,30,374 prompt characters:

| Round | Old cache / input | New cache / input |
|---|---:|---:|
|0|4,096 /22,871 =17.91%|6,144 /22,871 =26.86%|
|1|5,504 /22,873 =24.06%|45s transport timeout; usage unknown|
|2|5,504 /22,869 =24.07%|22,784 /22,869 =99.63%|

Observed token-weighted rate across valid usage:old22.0133%(3/3),new63.2444%(2/3).
This is a SMALL synthetic observation, not proof that whole live traffic will maintain63%
or99%; one arm has an incomplete sample. New prefix allows reuse in this setup, but
rolling120 history still drops oldest entries and breaks history-prefix reuse. This
observations-first experimental result is NOT the final conversation-first live result.
The narrowed candidate needs its own measured result; preserving source priority takes
precedence over this synthetic cache gain.
No promise of fully cache-stable context is made. Future improvements require a separate
conversation-history design that preserves freshness and bounded resource behavior.

## Regression and rollout requirements

350 tests passed,17 platform-specific skipped,18 subtests passed; pyflakes,compile and
release scan clean. Includes complete-context Unicode round-trip; prefix invariants when
only time/history/messages change; native tool/result pairing; multiple screenshot rounds;
same-path fresh screenshot acquisition; usage unknown/zero/invalid/credential allowlist.

Commit-only staging, baseline hashes, complete backup, exact-plugin reload/drain, unchanged
AstrBot/VM/NapCat processes, group-policy hash check and temporary plugin-key revocation.
After activation, inspect real model_usage logs; do not treat isolated tests as live-traffic
cache confirmation. Audit warnings and independent send-tool targeting limitations are
recorded separately and are not silently changed inside this cache optimization.
