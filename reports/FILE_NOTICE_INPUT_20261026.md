# HTML file/empty-notice incident — 2026-10-26

## Actual timeline

Screenshot file is deepseek_html_20261026_67fee3.html,46884bytes (UI45.79KB).
At20:04:13UTC+8, a synthetic UUID event224584d615ae407fb951b1c194a0fc6c entered with
no components/text and started a one-entry model batch. Native file1624335508 arrived
about0.45seconds later; it was correctly recognized by deployed nontext_context as file
metadata with filename,file_id,file_size and URL. Native quote/text2104472957 at20:04:19
references1624335508 and says the file is a web version. Empty-notice reply was produced
20:04:28; native file+caption then formed the next two-entry batch and HTML-aware reply
20:04:42. This occurred AFTER the earlier19:46nontext deployment, not an old screenshot.

Installed adapter _convert_handle_notice_event constructs GROUP_MESSAGE,empty message and
message_str,UUID,raw_message=event. Installed NapCat group-upload source constructs notice
post_type/notice_type and file{id,name,size,busid}; notice file.id is composite and not
necessarily equal to native segment.file_id. Exact historical UUID raw notice was not
retained in INFO logs; attributing that event to group_upload is supported by installed
source+same-file timeline, not a recovered exact raw payload. No file contents were fetched,
downloaded,opened,rendered or executed. Credentials/private source bodies not published.

## Narrow local fix

OneBot post_type=notice wrappers are source observations, not additional user utterances.
Known group_upload with a nonempty file dictionary is normalized through the existing pure
nontext adapter as typed file with the entire nested source file unchanged. Unknown or
attachment-free notice wrappers do not create fabricated empty history/model opportunities.
Recognized upload metadata is retained in history but does not enter the reply queue. Native
file messages and quoted text still follow normal saved policy and lifecycle batching.

No timer-based deduplication or guessed equivalence of notice/native IDs is used. Repeated
real file/chat messages retain independent valid opportunities. Images are not invented for
files. Ordinary real empty chat behavior is not globally removed. Readiness-wait stopping
recheck is retained as a fail-closed guard, not a claim the existing reload issue is repaired.

## Verification

Pure group-upload regressions preserve all unknown/type/data metadata and input immutability.
Eight new integration cases cover history-only upload notices,ignored generic notices,busy
notice admission,real file+quote batching,sequential legitimate messages,real empty/text.
Related156tests passed after removal of incomplete lifecycle changes; final full suite run
recorded with commit. Static/compile/release validation required before commit.

## Deployment explicitly withheld

Prior provenance release3aa0df0 failed initializing20:34:14 and restored disk files to6bcf3cb.
Temporary access was revoked using a fresh SSH connection. Current process714988 remains
active withNRestarts0, but that alone does not establish a healthy plugin instance. Prior
private journal contained active VM tool-loop calls, not just idle background heartbeats;
therefore do not interrupt user work or declare the drain defect fixed by source guards.

An incomplete lifecycle patch was deliberately removed from this change and not committed.
Latest five-minute read showed no matching activity but is not an active-instance idle/health
acknowledgement. No new deployment/reload/restart/QQ send was attempted for this file fix.
The fix is local and tested, not active. Restore/verify plugin runtime through the reviewed
native lifecycle before a new guarded release; do not claim that file restoration alone
proves plugin recovery or use restart as a shortcut.
