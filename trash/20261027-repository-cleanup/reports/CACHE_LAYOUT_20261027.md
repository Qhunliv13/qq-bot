# Lossless cache-prefix optimization and complete diagnostics

## Measured baseline

Read-only production sample2026-10-27 11:15:57UTC+8,weighted cached/prompt token sums:
12h224requests43.698%(4,304,128/9,849,767);3h100requests50.622%;1h59requests45.318%;
30min24requests39.157%. Latest image release receiptmtime11:05:45;9requests/612seconds
26.396%,too short/mixed to infer release causality. All usage had cache_valid=true.
System/tools were unchanged in three available repeated-scope postrelease observations.
Recent100toolobservations in one active scope serialized123,348UTF8bytes. Available native
contexts were146–149KB with28–31KBhistory andonly28–30KBreused user prefixes. Evidence
archive count/size corroborates a large volatile-tail candidate,not a unique HMAC scope join.
Historical per-request evidence-unchanged rate cannot be reconstructed and is not invented.

## Root and change

Existing layout already preserved120–239chronological history entries before current messages;
clock was at the end. No historical-window enlargement/chunking/padding is needed. However
large observations followed conversation.messages,so a fresh current message invalidated the
entire evidence tail even when its123KBbytes were unchanged.

One deterministic top-level order now emits version/source/observations BEFORE conversation
(scope/history/messages/anchor),thenexistingother_scopes/task/attachments/timing/trigger/activity.
Decoded JSON fields,values,string/list order,source labels,quotes,images,task state,raw persona,
fresh clock and95native tools remain intact. No split frames,duplicate keys,protocol/schema
version change,adaptive sorting or context deletion. Wire order does not grant source priority,
authority or trust. Positive cases:unchanged evidence with fresh current/history/clock,appended
evidence below cap,andhistory block rollover. Explicit negative case:100record evidence window
evicts its first record,so evidence-first loses the history prefix behind that changed evidence.
This is a workload tradeoff,not a claim of guaranteed population cache-hit improvement.

## Observability repair

Native initial vision messages contain envelope text plus separate image-provenance text.
Old profiler rejected multipletext messages as unavailable. It now selects exactly one valid
native envelope amongall first-user text parts; ambiguous envelopes fail closed. Every ordered
text/image part,andall subsequent messages,enter additional keyed prefix measurements without
modifying payloads. Legacy single-text detection remains; source labels are never removed.
Profiler state contains process-random HMACdigests/lengths only. Numeric request_index associates
request prefix telemetry and usage even for out-of-order completion; absent/invalid IDs remain
unknown. Indices are process/instance local,notuser/provider IDs andnotunique acrossreloads.
Diagnostic canonical JSONbyte lower bounds are NOT provider token/cache counts or exact HTTP
wire ordering. Provider backend affinity/retention/routing remain outside these measurements.

## Tests and deployment

1483passed,24platform skips,25subtests. Exact UTF8 prefix comparisons cover six representative
layouts including cap eviction;all-fields roundtrip and invalid-JSON rejection;multi-text image
provenance,native ambiguity,full ordered continuations,privacy andout-of-order numeric usage.
Static/compile/release checks clean. Actual provider A/B uses same private existing history/
observations/persona/tools andchangingdiagnosticcurrent request;raw content neverprinted,
no returned tool actions executed,no QQ messages. Diagnostic max_tokens128 onlybounds test
output;productionreasoninghigh/max_tokens6000andrequesttimeoutsremainunchanged.

Deploy committed bytes→baselinehash→backup→exactnativepluginreload→liveverification→scoped
accessrevocation,without service restart or taskcancellation. A one-off requestset isnot a
long-term postdeploymentweighted ratio;see separate receipt for actual A/B andlive results.
