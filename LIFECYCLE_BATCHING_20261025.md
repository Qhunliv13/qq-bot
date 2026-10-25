# Processing-lifecycle batches and positive reply selection

## User-visible semantics

No fixed batch window, sleep, or HIGAN_BATCH_WINDOW lookup remains in message admission.
The first accepted message can start a turn immediately. Messages accepted while the
current model/tool/send operation is running remain queued. Once that operation has
finished, all queued messages for the scope form the next decision. The shared execution
lock can additionally accumulate accepted messages while another scope/task is running.
The snapshot already submitted to a model is not mutated mid-request, and messages are
not replayed or allowed to cancel an active tool/send operation.

The system protocol describes a positive selection:
`{"reply_to":["actual-current-message-id",...],"text":"answer"}`.
The model chooses messages to answer, not a blacklist of messages to ignore. One response
can address several selected messages; an empty selection/text or SILENT is valid silence.
IDs come from actual source records, never a nickname heuristic. No requirement to answer
all messages, recognize specific senders, or stop after a fixed number of replies was added.

The pure reply_selection decoder validates the selected IDs and unwraps text once before
sending. If any selected target is earlier than the final current record, a native Reply
quotes the first such selected target, even when reply_to starts with the final ID. A
last-record-only response has no automatic quote. One platform Reply component cannot
quote multiple targets, so combined response prose may distinguish the other targets.
Explicit selection is the source of truth; an embedded legacy quote marker cannot override
it. Previous-turn quote state is cleared. Legacy plain text and explicit quote markers
remain compatible; plain prose alone is not evidence that an earlier message was selected.
This compatibility does not guarantee every upstream completion uses structured selection.

## Validation

327 tests passed, 17 platform-specific skipped, 18 subtests passed; pyflakes, compile and
release scan clean. Tests gate real model I/O and event.send with asyncio.Event rather than
clock sleeps: first message -> model/send in progress -> two arrivals -> next batch of two.
Another regression rejects any old batch-window environment lookup or collection sleep.
Six end-to-end fixture cases exercise on_message -> _handle_one -> decoder -> _send:
earlier target101 -> native Reply101, last target102 -> no automatic quote, ordered targets
[102,101] -> Reply101, stale quote cleared, embedded quote999 cannot override selection,
and outgoing text is answer rather than protocol JSON. All delivery fixtures are isolated;
no QQ test messages are sent.

## Rollout

Initial code commit 56905757746846ddc978aa99e29ca6ebf621848d was activated through exact-plugin
reload with five file hashes checked. AstrBot PID714988 and NapCat start time were unchanged;
group policy bytes were unchanged and temporary scoped permission was revoked. Earlier
preflight attempts stopped before mutation on obsolete baseline and reused credential-file
path. The successful attempt uses its own commit-scoped release directory and backup.

Follow-up hardening strips conflicting legacy quote markers from structured text and adds
actual selected-ID reporting to the real-model probe. A successful process exit alone is
not proof of structured selection or correct quoting; report these fields explicitly.
