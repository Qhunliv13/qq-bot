# Post-repair log audit — 2026-10-26 14:50 UTC+8

Read-only audit of active code8fb7f8a, release2026-10-26T12:36:03+08:00.
Collector cutoff2026-10-26T14:50:27+08:00. Full journal JSON decoded including byte-array
MESSAGE values;1048records actual span12:36:03.561–14:50:09.399. No production edits,
reloads, QQ sends, provider test calls, policy/billing/channel changes or credential output.

## Confirmed remaining defects / limitations

### Native send receipt contract is incompatible

Actual installed AstrBot AiocqhttpMessageEvent.send184–198 returnsNone. Its send_message
and _dispatch_send also discard send_group_msg/send_private_msg results. Plugin
main._send3493 expects `await event.send(chain)` to return iterable receipts; iteration at
3501 seesNone, so _last_sent is never populated by this normal reply path. Consequently
_recall_last3531 lacks real IDs for normal replies. There are zero success message_id
receipt log lines, consistent with this source contract. This does NOT mean no messages
were sent; absence of send exceptions is not a platform delivery receipt either.

No platform send/recall was executed to test this during audit. A later repair must retain
exact native serialization, routing, quote/media behavior, record actual returned IDs and
avoid duplicate fallback sends after an uncertain acknowledgement.

### Forward image coverage silently loses access to most images

Three real forward reads were successful after the last fix:

| Time (UTC+8) | Parsed leaf items | Available image references | Initial selection ceiling |
|---|---:|---:|---:|
|13:19:35|310|91|last3|
|13:35:35|57|7|last3|
|14:39:03|79|5|last3|

Read-only get_msg and local exact current parser replay agree. The old empty_forward
regression is not present in these3records. However main.on_message1295 keeps only the
lastMAX_IMAGES_PER_CALL refs, currently3. image_transcript.with_images applies the same
batch ceiling. Full metadata in the text is not full visual access. The model has no
explicit image-coverage record or generic source-image reader for omitted refs. All41
observed completions made zero tool calls, so the later forward-tool image append path
was not exercised by real traffic. There are no conversion-drop markers in this window,
but no retained model payload receipts prove even the selected3were visually interpreted.
Do not report that all91images were read or that parser success proves semantic reading.

### Image-description timeout

One warning at14:04:29.581978: _describe_image caught asyncio.TimeoutError, returned empty
description. No traceback. It is separate from forward parsing and direct conversation
model transport. Do not count it as a whole-turn model timeout or infer all images failed.

## Runtime reconciliation

172incoming messages=115saved-policy blocked+57accepted. Accepted messages form54batches:
41modeled turns+13probability skips. No observed model transport, batch, send exception,
invalid selection or traceback. All41tool_calls=0; tool execution success is not proven.

Model completion latency4.224–30.411s,median15.939s,mean15.578s. Fourteen accepted batches
waited>1s;maximum20.176s. Cross-scope head-of-line wait is reproducible under the existing
shared execution lock (e.g.15.099s). This is the deliberately shared serial execution
policy, not grounds for adding rejected arbitrary time/round caps or changing batching.

Forty of41modeled output lengths match a nearby persisted bot-history row within5s.
One25char output at14:47:18.491 lacks that simple match. Capture timing, rendering,
cleaning or policy may explain it; not established as send failure. Shared history snapshot
has43bot replies including later rows outside journal scope; do not compare43to41directly.
Metadata-only scan found no code fences, Python statements, assistant/internal/DSML tokens
or tool/reply_to JSON leak candidates. This does not establish universal model correctness.

## Actual cache use — not synthetic probe traffic

41valid model_usage rows, allpositive reported cache:
- Input2,079,807tokens; cached959,872; uncached1,119,935.
- Token-weighted hit46.1519747%, compared with previous unmatched48request window31.4040888%.
- 12h3requests61.6295%;13h30requests40.7587%;14h8requests62.4467%.
- Last20requests49.1734%.

Cache usage improved numerically, but total input averages50,727tokens/request versus
17,221in the previous sample. Uncached input averages27,315versus11,813. These windows
have different actual forwarded content (including310source records), history lengths,
image modalities and source mix, so do not causally attribute token growth only to the
stable window. A larger hit percentage alone does NOT prove cost savings. No provider
prices, bills or channel routing were changed or assumed.

## Disposition

This request was log inspection. Production remains unchanged. The two actionable source
issues above are recorded; no unverified transport rewrite or forced all-image load was
shipped. Real repairs should be narrowly regression-tested with installed-framework
contracts and exact source metadata, not only simplified event mocks. User-visible bug
not covered by these findings still requires its actual message/time/screenshot to identify.
