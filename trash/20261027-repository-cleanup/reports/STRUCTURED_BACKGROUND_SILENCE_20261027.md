# Structured background silence: genuine delivery-boundary defect

## Observed production evidence

2026-10-27 UTC+8 read-only investigation confirmed9native bot messages with exact empty
reply envelope in the principal group; get_group_msg_history followed by individual get_msg
returned native numeric IDs withstatusok/retcode0. Times:Oct26 23:34:21,23:54:54;
Oct27 00:56:48,01:58:13,02:29:03,02:59:17,03:29:56,05:01:55,05:32:23.
All12history files contained21exactbot empty envelopes across3scopes. These align with
current_batch_entries=0 numeric telemetry and autonomy_turn status=completed,not evidence
of durable task leaks. Temporal correlation lacks unique request IDs; do not assert exact
screenshot-four identity from nine matching native messages. Original user payloads omitted.

## Root cause

Foreground _handle_message decoded current-batch selections before send. Autonomous run_turn
had messages=[] and passed raw model text through only is_silence then _send. Shared decoder
intentionally retained all JSON when records empty; _send recognized only whole SILENT text,
not the structured reply envelope. Therefore {reply_to:[],text:''} was displayed literally and
recorded as successful delivery. Broadening text-substring deletion would mask the bug and
break valid literal JSON examples; do not modify persona/model behavior to compensate.

## Incremental repair

reply_selection.decode_reply gains keyword-only allow_unattributed=False. Explicit no-current
callers opt in; only a whole direct object with BOTH reply_to and text enters protocol.
No-current targets must be[] and text must be string. Empty/control token yields silence;
nonempty text is a valid autonomous response without a quote; fabricated targets fail closed.
Default no-batch legacy decoding remains unchanged; current-batch ID validation unchanged.
is_silence itself is not broadened to swallow objects,fences,prose or nested serialized JSON.

Autonomous and task model completions use the shared decoder before deciding delivery; a
silent autonomous turn returnscompleted/deliveredFalse and task silence does not falsely
pause as failed delivery. The sender also guards raw entry paths using the same decoder.
Already-decoded payloads are marked decoded=True so the literal text inside one envelope
is not parsed a second time. Existing fourth positional quote argument stays compatible.
Foreground sender passes decodedTrue after its original selected-target validation.

No literal output replacement,forced reply text,persona rewrite,additional model call,topic
heuristic,autonomy disabling,public test message or service restart. This repair does not
implement the separate pending history-window/cache/timer/outcome recommendations.

## Verification

1083passed,17platform-dependent skipped,18subtests. Pure decoder318tests;30new background
integration regressions cover actual_send,TaskEvent,_run_task_once,real controller+run_turn,
current-batch attribution and provider-backed foreground nested literal. Old test send doubles
updated to assert exactly-one-decode marker,not to relax delivery/claim checks. Static checks,
compile and release scan clean. Deployed source probe invokes actual sender without any
model/platform path,tests5silent/invalid envelope cases and checks nested literal remains.

Deployment: committed source→baseline8eddb39 hashes→backup→native exactpluginreload→checks→
temporary access revocation. Preserve group policy bytes and process identities. Deployed
receipt recorded separately; no claim that every possible future model prose is silence.
