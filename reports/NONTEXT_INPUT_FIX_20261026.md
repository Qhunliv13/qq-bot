# Non-text source preservation — 2026-10-26

## Exact screenshot evidence

Message1817393297 at2026-10-26 19:30:54 UTC+8 contains one OneBot face segment,
id489. It is not image,mface,marketface,JSON or an empty payload. Erroneous assistant reply
at19:31:13 is persisted after the earlier19:30:59 expiry reply matching the screenshot.
Read-only get_msg succeeded. Exact segment shape:
`{type:face,data:{id:string,raw:object,resultId:string,chainCount:number}}`.
resultId is empty,chainCount0. raw retains faceIndex,faceText,faceType,packId,stickerId and
other genuine QQ metadata; no image URL is supplied. No private chat or raw metadata values
are published here beyond the incident's type/id and structural facts.

Actual installed AstrBot Face declares only id; normalized Face survives but does not add
to get_message_str. Extra fields remain in message_obj.raw_message.message. mface is dropped
by this adapter and unknown types can be omitted. Main previously handled rendered text,
Image components and separately expanded forwards; a face-only source became no-body text.
The model was therefore given missing source context; this is not a user sending air.

Production23Python-file hashes exactly match aeb4975before this repair; no unknown drift.

## Shared pure non-text adapter

nontext_context.describe_event preserves top-level source segments as typed untrusted data.
Raw OneBot current-message arrays are authoritative for their types; canonical-only types
supplement them. This avoids cross-representation duplicates but preserves repeated actual
source segments. Text/mentions/replies/forwards remain in their established handlers; no
nested quoted At can become current wake authority. Ordinary plain input is unchanged.

Native face metadata, image/mface, voice/video/files,cards and unknown attachment types are
retained without invented text/pixel meaning or arbitrary payload count/text truncation.
Non-JSON objects/cycles are omitted instead of being stringified as false evidence. Cards
are data, not executable instructions, and nested card URLs are not guessed as images.
Only explicit image/mface reference fields can enter the existing visual pathway. A face489
id never generates a guessed external image URL. Missing/failed pixel access is not silently
replaced by unrelated previous-group images. Actual per-request image cap remains unchanged.

Main appends this source to the existing text input before history/batching and associates
refs with that exact event. Its pending image flag includes genuine event refs, preserving
normal message ordering, current source IDs, saved policy, command handling and silence.
No persona changes, image-specific response rules or group/id-specific behavior added.

## Actual-source-to-model proof

Candidate process reads the real existing message via OneBot get_msg and actual provider
configuration. Actual _request_context and _llm_tools receive source metadata and return
face/id489 correctly: recognizedtrue,segments1,image_refs0,allmetadata preserved in provider
context,real model recognizedtype+idtrue,26answer chars,0requested tools,0QQ sends.
This proves source metadata arrival, not pixel understanding or a guarantee of future prose.
Separate end-to-end tests run unchanged on_message through batching/native loop into captured
model payload; no original source is lost and no empty-source fallback is inserted.

## Regression and release

670passed,17platform-dependent skipped,18subtests; pyflakes/compile/release scan clean.
68pure adapter cases plus11integration cases cover actual face489 raw/canonical metadata,
raw mface dropped by adapter,image multipart,announcements without fake forward lookup,
files/video without invented vision,ordinary plain,mention authorization and commands.
All prior silence,forward,history,tool and task tests remain passing.

Release uses committed bytes, aeb4975baseline hashes, backup and drained native plugin
reload. No framework edits, service restart, policy change, QQ test sends or credential
output. Final activation/native verification evidence is recorded separately.
