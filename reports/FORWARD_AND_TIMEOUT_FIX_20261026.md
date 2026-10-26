# Forwarded chat and failed-completion fix — 2026-10-26

## Exact screenshot evidence

Incoming message1804088023 was sent2026-10-26 11:19:23 UTC+8, persisted11:19:25;
empty-message reply persisted11:19:52. Actual OneBot message is forward with resource
7700810920543486448 and five inline data.content nodes. First node nests34image messages;
other four are text. Inline and actual get_forward_msg(id=...) data.messages are equal.
Current parser resolves both inline mappings and AstrBot id-only Forward to38genuine leaf
items (34image,4text). Native id-only mode uses one get_forward_msg call, inline uses none.
No private chat/image URLs/credentials are included in this report.

Napcat actual HTTP is container3000->host127.0.0.1:13000; host3000 is nginx, not Napcat.
Old _forward_to_text looked for dict comp.data.id, missing AstrBot comp.id/type enums;
its HTTP fallback used the wrong service, while forward_expand duplicated a separate
formatter. Nested nodes and metadata were dropped or capped40x300chars. Ordinary JSON
announcements are NOT forwarded chats; earlier empty message1819689112 was an announcement,
not this screenshot's forward, so that separate diagnosis must not be conflated.

## One shared implementation

plugin.forward_context now handles admission, read_forward and forward_expand through the
original event.bot.api.call_action connection. It recognizes enum/dict Forward, embedded
Nodes/Node, data.content/node.message, raw original events, genuine multimsg JSON/XML
resid/m_resid cards, direct/enveloped native responses and failure retcodes. Ordinary
JSON/card IDs never trigger a guessed forward fetch. Recursive forwards preserve full
supplied text, source sender IDs/display names and media metadata. Per-resolution cycle
and repeated-fetch protection prevents recursive loops, not arbitrary text truncation.

Admission carries typed untrusted forwarded data even on a failed recognized read, rather
than labeling a card as an empty poke. event-bound resolved image refs feed existing
batch image/vision selection; they are not globally swapped with another sender's images.
34image metadata records do not establish that all pixels were seen. Existing per-request
image transport constraints remain, no speculative all-image OCR success claim is made.

## Timeouts and failure semantics

Actual selected provider source timeout=300seconds. Direct model calls previously always
used plugin CHAT_TIMEOUT45, causing premature disconnects. The selected provider's
validated explicit timeout now supplies the HTTP transport deadline. Missing/invalid
settings retain the existing fallback; explicit zero means no per-request deadline.
No arbitrary larger default, whole-turn cap, model retry loop or enforced tool use added.

Agent loop makes at most its existing one no-tools finalization request after failure.
If that finalization also fails, returns forced empty, or violates disabled-tool mode,
it raises typed ModelCompletionUnavailable(reason,used_tools,tool_attempt_count), retaining
the attempted tool observations and exception cause. It no longer returns/appends the
canned SAFE_FINAL text. Main logs failure counts without raw content, chat sends no
fabricated final text and does not start another independent model loop; task continuation
raises task_finalization_failed for existing scheduler pause/review, not false success.
Ordinary model-directed empty output remains valid silence. SAFE_FINAL is import-compatible
only, no new successful response returns it. A source constant alone is not an active path.

## Verification

405tests passed,17platform-specific skipped,18subtests; pyflakes,compile,release scan clean.
Includes29pure forward cases, actual-fixture offline verification, native admission/tool
integration, recognized unreadable forwards, two consecutive synthetic timeouts with zero
or one prior tool, no duplicate tool execution/no independent fallback, task failure state,
provider300timeout preservation, explicit zero/invalid settings and full previous regressions.

Release uses committed files, previous17f3dc4baseline hashes, backup and native exact-plugin
reload/drain. No service restart, group-policy change or QQ test messages. Actual post-release
hashes and native read-only verification belong in separate deployment evidence.
