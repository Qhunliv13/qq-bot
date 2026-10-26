# Silence control repair and cache-prefix diagnostics — 2026-10-26

## Actual failure evidence

Two persisted assistant payloads are exactly `（SILENT）` (eight Unicode chars, fullwidth
parentheses), at2026-10-26 14:49:35+08 and14:59:21+08. Matching native model output records
report8characters. Production main/reply_selection bytes match8fb7f8a. This is an actual
leak, not a hypothetical lowercase or structured response; those latter forms are offline
regression cases only. First batch started14:49:09.527959; second14:59:00.507944. A later
incoming message14:59:07 belonged to the next batch, not the one producing the leaked token.

The old early check uppercased/trimmed selected sentence punctuation, not parentheses.
decode_reply recognized only exact uppercase standalone SILENT and did not normalize the
text within an otherwise-valid structured selection. _send lacked any silence guard.

## One explicit control boundary

reply_selection.is_silence is shared by early/fallback decisions, scheduled paths, structured
reply decoding and send defenses. It recognizes missing/blank output or a whole SILENT token
case-insensitively, established sentence-edge punctuation, one valid JSON-string layer, and
one matching ASCII/fullwidth parenthesis pair around the token. These are transport forms,
not semantic silence guesses. Nested/mismatched/unbalanced parentheses, code fences, prose
containing silent, arbitrary action objects and other wrappers remain ordinary content.

Structured reply targets are validated before considering their text; silence cannot bypass
invalid attribution. A valid selected silence has empty text and no quote. Final send checks
run before side effects and after cleaning/quote-marker processing; empty image-only text
is not mistaken for a silence instruction. No retry/extra model call, tool rollback, persona
rewrite, batching delay, forced response or behavioral cap is introduced.

## Cache evidence and unresolved boundary

Current logs12:36:03–15:18:50+08 contain51valid usage rows:2,643,393input /1,246,848cached,
weighted47.1685%. Last30minutes:12requests689,145input/390,528cached=56.6685%.
These are real traffic, not synthetic warmup and not a guarantee or completed optimization.

Older same-release41request window46.15197% hides different scopes: inferred nearest-batch
correlation gives21requests25.261% in one scope and18requests67.217% in another. These are
scope differences, not proof of upstream worker sharding. Tools64, deterministic13,739chars
(16,047UTF8bytes); runtime task state does not modify schemas. Persona1290chars per reload,
not an invented giant static prefix. Clock already last and history already block-aligned.

Exact historical request reconstruction from disk is impossible: legacy archive clips each
stored line to2000chars while live history retains full forwards. Do not use clipped archive
replay to claim an actual cache miss location. Different actual forwards and images also
change token use. No new padding, larger history windows, provider affinity guesses, cache
warming or arbitrary cache-control request parameters were added.

## Privacy-safe actual-request telemetry

prefix_metrics observes the exact constructed native payload before transport without changing
it. Process-keyed HMAC digests retain only byte lengths/block digests for each opaque scope;
logs expose numeric/boolean availability, local scope index, first observation, system/tool/
image unchanged flags, image count, history/current entry counts, history/user UTF8lengths,
and previous-prefix reusable byte lower bounds (256byte resolution). It never logs request
text, image URLs/data, actual scope IDs, prompts, secrets or digests; no raw bodies are retained
in tracking state. Missing/malformed/unscoped data reports unavailable. Diagnostic failures
log exception type only and cannot block/change the model request.

Byte-prefix reuse is local evidence, NOT provider cache tokens or inferred backend identity.
Images and system/tools are compared separately. This instrumentation diagnoses a remaining
problem; it does not itself increase hit rate. Any later optimization must follow measured
actual-prefix and provider-usage evidence and consider absolute uncached tokens, not just%.

## Verification and release constraints

591tests passed,17platform-specific skipped,18subtests; pyflakes/compile/release scan clean.
Observed fullwidth leak has end-to-end tests through on_message/_handle_one/_send, structured
and JSON forms, cleanup/quote boundaries, valid image-only outputs and tool-then-silence.
No sends, history writes or cooldown updates occur for silent turns; tools are not repeated.
Sixteen prefix tests cover stable/rolling histories, system/tools/image changes, invalid
payloads, scope isolation and request/state secrecy.

Release must use committed bytes, backup, baseline8fb7f8a hashes, drain and native plugin
reload. No service restart, group-policy edit, billing/channel changes or QQ test sends.
Actual deployed evidence and postrelease cache observations are recorded separately.
