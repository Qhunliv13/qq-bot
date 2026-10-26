# Actual forwarded-source and historical-prefix repair — 2026-10-26

## Why the previous release was not sufficient

Actual production window11:43:58–12:23:18 UTC+8:48valid usage requests,826593input,
259584cached=31.4040888% weighted. One model transport failure and two associated traceback
headers occurred; no batch/send failures. Seven actual forward attempts in11:44–12:25
returned empty_forward. Previous synthetic tests omitted full NapCat node event metadata.

Latest affected incoming message978732783 at12:18:12, assistant empty classification12:18:32.
Direct read-only NapCat get_msg/get_forward_msg return21outer full source records, including
one nested forward32records. All21 outer nodes carry post_type/message_type/self_id and
message arrays. Previous a69a6aa's ordinary-event guard rejected these genuine nodes.
This is a regression in our parser, not missing user content or an upstream empty result.

## Actual source fix and proof

The same parser now tracks structural node context. Ordinary top-level raw message envelopes
remain containers, but records inside a proven forward payload retain sender/text/media
regardless of their event-like fields. Embedded raw source nodes take precedence over lossy
normalized Forward(id), avoiding needless API reads and preserving original signed refs.
Native API fallback uses the same parser in forward-node context. No per-group/media-ID
special cases and no guessed fetch from ordinary cards.

Private actual-fixture replay recovers52leaf records, identical text/sender/media-type order
from inline and API responses. Initial replay counted8media attachments; an independently
refreshed actual-source-to-model probe reports7image attachments (media and image counts
are distinct, signed media metadata can differ). All44nonempty source texts survived JSON
serialization into the candidate's actual _request_context. The actual configured model
quoted one exact source text with tools disabled; zero QQ messages sent. No source bodies,
image URLs, participant identities or credentials are published. First3actual QQ image refs
were independently HTTP200 image/jpeg below existing4MB/8s conversion limits.

Initial and explicit read_forward/forward_expand tool results both feed image refs into the
existing visual pathway. Images obtained by tools are appended only after their native
assistant/tool pair. Existing per-request image limits remain, so no claim that every image
was visually inspected. Plain top-level messages stay plain, no invented forwarding wrapper.

## Stable chronological history prefixes

Timing-last alone cannot cache a history whose first line slides on every new message.
The existing120-entry minimum is retained. Snapshots start at chronological blocks derived
from this same window and include120–239entries; every original latest120entry survives,
source strings are unchanged, current batch remains separate. This retains a short extra
older suffix rather than deleting/compressing context, freezing time or indefinitely growing
context. Unequal entry lengths mean token growth need not be proportional to entry count.

Logical eviction origins accompany the existing2000-entry live buffers. All actual writers
(incoming messages, confirmed outgoing replies, membership observations) use one append
helper, and origins transfer with history on drained hot reload. Pending batches retain the
first-arrival frozen history instead of rebuilding from later live state. Archive retention,
reply timing/admission and tool budgets are unchanged.

## Real model cache comparison

Both variants use current timing-last ordering and same provider/system/tool catalog/dataset.
Old uses sliding latest120; new preserves same latest120 plus0/1/2older entries over3rounds.
No QQ sends or tool execution. Provider cache not flushed; first paired prompts are byte
identical and cross-warm, so warm rounds are the meaningful comparison, still small/uncontrolled.

| Metric | Sliding | Stable |
|---|---:|---:|
|All input tokens|65406|65804|
|All cached tokens|13824|63872|
|All uncached input|51582|1932|
|All weighted hit|21.1357%|97.0640%|
|Warm input tokens|43604|44002|
|Warm cached tokens|9728|42112|
|Warm uncached input|33876|1890|
|Warm weighted hit|22.3099%|95.7047%|

This establishes a matched small-history experiment, NOT a guaranteed live percentage or
money saving. Total input, uncached input and postdeployment traffic must all be reported.
Most cache-probe answers did not exactly match requested391; this diagnostic measures cache
usage, not semantic quality. The separate actual-forward probe verified exact source quoting.

## Regression and release

439passed,17platform-dependent skipped,18subtests. Static/compile/release scan clean.
One initial full run hit Windows WinError10055 creating an asyncio loop; unmodified rerun
439passed, distinct from product failure. Tests now reproduce real full NapCat nodes red
before fix/green after, exact provider body, native tool image pairing, ordinary-event
nonforward behavior, origins across6500appends/live2000evictions, actual three-writer and
reload integration. Deployment is committed/hashed/backup/drained native hot reload only;
no service restart, no policy edits, no QQ test sends. Final deployment evidence recorded
separately after native verification and permission revocation.
