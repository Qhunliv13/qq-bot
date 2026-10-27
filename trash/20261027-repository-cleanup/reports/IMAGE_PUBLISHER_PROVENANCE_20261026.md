# Image publisher and quote provenance — 2026-10-26

## Exact incident, not inferred from screenshot order

Actual image1008020995 was sent19:42:24 UTC+8, processed19:42:26.521591 as one message/one
image. Actual publisher is a member, not either configured owner. Bot127847517 sent19:42:44
(persisted19:42:45) incorrectly called the member owner and characterized the picture as
bragging about scores. Bot output has no native Reply segment. Caption1394073510 selfquotes
the image and was physically sent19:42:43 but ingested19:42:46, after the in-flight call;
next batch started19:42:46.497409. Therefore that caption was not input to the offending
reply. Do not describe the bot as answering the later caption or an owner/member mixed batch.

Both ingress identity logs correctly say member, not owner; raw native sender.role=member.
The persona read-only SELECT explicitly distinguishes the two owners and prohibits calling
other members owner. No persona change or forced term replacement is appropriate. The
incident predates the previous nontext patch commit19:45:04/filemtime19:46:38, but that patch
also did not guarantee correct identity wording. Original full model payload was not retained;
numeric metrics show181history entries,onecurrent message,oneimage. Cannot prove every
internal cause of the generated wrong attribution from source alone.

## Confirmed source transport gap and narrow repair

Existing per-current-message sender.role lookup was correct and is retained. Visual refs
were flattened from current,quoted,forwarded and nontext sources into bare strings across
all events. Image conversion created unlabelled multipart images; generic _refs_for description
was discarded. Current source images may have been indirectly inferable from raw URLs in
text, but quoted/multi-message/data-URL correspondence was not explicit.

attachment_sources.collect_sources is now the single pure image-source extractor. _image_refs
is its compatibility ref projection. Each occurrence preserves original ref, actual source
message ID/publisher, relation current/quoted/forwarded and containing/quoted IDs where relevant.
Unknown quoted/forwarded publishers remain unknown, never borrowed from current sender. Same
ref from different messages preserves all source relations before transport dedup/cap. Captured
real adapter uses Reply.sender_id; no speculative scalar-sender repair was introduced.

Batch freeze includes selected image source records alongside message/history. _request_context
adds optional attachments.origins while retaining attachments.image_refs and default no-origin
wire shape; quote/source roles are assigned from their own actual IDs, unknown remains unknown.
with_images accepts optional source_records and emits neutral untrusted provenance JSON directly
before each successfully converted actual image. Capped/failed images receive no misleading
labels; same converted image merges all source origins. Existing multipart parts and native
assistant/tool pairs remain unchanged, including refreshed same-path tool screenshots.

Native and no-tools fallback use the same image assembly. Image-source labels describe the
publisher of the source message, not photographer, account owner, depicted subject or score
ownership. No OCR, score rewriting, hardcoded user/group response, keyword output filter,
persona rewrite, forced address or extra model decision was added. This narrows ambiguity;
it is not a guarantee against all future model inference errors.

## Tests and validation

692passed,17platform-dependent skipped,18subtests; pyflakes/compile/release scan clean before
probe addition. Tests cover member/current image, member quoting owner,owner quoting member,
separate actors in a batch,sameimage multiple sources,unknownquote identities,source cap and
conversion failures,real fallback parity,no-image string shape,append-only image transcript.
Existing tests were updated only for new provenance text parts and explicit unknown quote role.
All original source numeric text, source IDs and persona content remain unchanged.

The separate actual-source probe reads incident image through native source metadata and
configured real model. It asks for publisher role in a controlled diagnostic, not a reenactment
or guarantee that the old natural-language reply will never recur. No platform test sends.
Deployment must be committed,baseline6bcf3cb hash-checked,backed up,drained and native-hot-reloaded;
no service restart or policy changes. Candidate and deployed proof recorded separately.
