# Native autonomous decisions and QQ social capabilities

## Scope

User request2026-10-26: improve model autonomy beyond passive wake; support QQ signature,
Qzone posts and related account actions, informed by source-available projects. No persona
rewrite, hardcoded topics, mandatory posting, fabricated owner roles or extra tool-round/time
caps. Existing group mute and mention-only permissions remain authoritative.

## Primary-source references (protocol/design, not copied code)

- MaiBot pinned062d45cf8bd4272ac57d7ec03fcf405e5a458211 reasoning_engine: native tool calls,
  actual result continuation and optional silence; proactive triggers recover context.
  https://github.com/Mai-with-u/MaiBot/blob/062d45cf8bd4272ac57d7ec03fcf405e5a458211/src/maisaka/reasoning_engine.py
- Signature official documentation: https://napcat.apifox.cn/226659186e0.md
- NapCat native Qzone fields/action reference, pinned public source3420f0b76d7dc0ac92cde1663f21ea27f61e13bb:
  https://github.com/NapNeko/NapCatQQ/blob/3420f0b76d7dc0ac92cde1663f21ea27f61e13bb/packages/napcat-onebot/action/extends/SendQzoneMsg.ts
- Qzone companion project architecture inspected, not vendored:
  https://github.com/Zhalslar/astrbot_plugin_qzone/tree/f474c50c9b517655d8475df34ad0c129b70193d4

MaiBot and the Qzone plugin useGPL-3.0; NapCat uses a restrictive noncommercial source
license, not an OSI-permissive license. This change independently implements an API client
and the existing bot's own shared-loop integration; no reference implementation or prompt
was copied. Installed NapCat4.18.37 was separately read-only verified online/good with
registered set_self_longnick,set_qq_profile,send_qzone_msg. Public main is not assumed to be
the installed version. No get_cookies/cookie extraction/new web session client is used.

## Decision flow

Old poll/wake_at had no tools and directly sent text outside the global execution lock.
Old free_tick was not enabled and had a separate one-shot tool path. The active poll loop
now collects typed opportunities, groups same-scope triggers, and calls autonomous_turn.run_turn.
It uses the SAME native model/tool continuation, shared execution lock, evidence archive and
sender as foreground/durable tasks. Busy chat defers without consuming an opportunity.
Fresh policy/history/clock/origin are checked under the lock. Explicit messages=[] remains
empty; trigger is data, not a fake human request. No extra planner model, response template,
fixed topic or forced action is introduced. Deprecated free_tick is a no-op compatibility
entry, not a second autonomous implementation. Existing optional loop/time limits unchanged.

AutonomousController stores only settings/scheduling/origin metadata in /root/higan/autonomy.json.
Real origins are learned ONLY from actual admitted message events, not guessed framework
strings or all QQ contacts. Missing origins defer safely. By default an unprompted opportunity
is offered every600seconds globally, round-robin among observed unrestricted scopes, starting
600seconds after the first real observation. This is editable opportunity cadence, not an
execution budget or required posting rate. poll_minutes and daily wake_at preserve their own
cadence. Daily slots now include UTC+8 date and persist across reload; no date-free dedup bug.

The model can inspect autonomy_status and set its next observation/cadence/enabled/scopes
through autonomy_schedule. Only actual owners or our typed internal AutonomyEvent may manage
that state. Timing is validated, not clamped into arbitrary ceilings. Opportunity ack is
persisted BEFORE model execution, preventing uncertain effects from being automatically
replayed after network/agent failure. Started is not a goal-completion/success claim.

## Social tools

All71 advertised native tools have explicit contract coverage (previous64 plus7).
- qq_account_info: native own identity and allowlisted descriptive profile; no credentials,
  phone/email/raw rich profile buffers exposed.
- qq_set_signature: native set_self_longnick, raw explicit text including intentional empty
  clear; acceptance and no_cache readback match reported independently.
- qq_set_profile: nickname,optional personal_note/sex only; no unintended full-profile rewrite.
- qq_publish_post: TEXT ONLY initial release,explicit visibility public/friends/selected/private/
  excluded; selected/excluded require explicit target IDs. Never inherit native defaultpublic.
  Accepted tid is not proof of published readback; installed source had no proven list/like/
  comment APIs, so those were not fabricated. Images/local paths rejected in this release to
  avoid arbitrary file disclosure/SSRF through native upload support.
- qq_social_status: declared backend capabilities and current write eligibility; not a false
  live-health guarantee.

Writes require actual source-owner identity OR typed internal autonomy with a real observed
origin and current permission. Member nickname/content/quote claims never grant authority.
Writes require operation_id and explicit runtime audit journal /root/higan/social.sqlite3.
Payload hashes and sanitized receipts are persisted, not signature/post content or cookies.
Concurrent/repeated same operation reuses receipt; changed payload conflicts; pending,unknown,
failed outcomes never trigger automatic re-send. Changing IDs to retry unknown outcomes is
explicitly not a safe retry protocol. No exactly-once guarantee against arbitrary new IDs.

Internal autonomous tool dispatch cannot rewrite group/wake permissions. Outbound tool target
must be previously observed and allowed; muted/mention-only destinations remain blocked.
qq_send omitted destination now resolves from actual scope, not hardcoded group146045691;
all qq_send paths recheck persisted destination policy. General VM execution remains the
existing powerful capability, not a sandbox; this change does not claim to prevent every
possible indirect effect through existing VM/browser code.

## Real-structure discovery during verification

The first real account-read probe FAILED despite offline tests. Diagnosis: the old shared
_onebot_http posted {action,params} to /, and installed NapCat returned status=ok,data=null
without executing it. Corrected single shared bridge to POST /action with params as body,
validated action path syntax and added exact URL/body tests. Social writes retain the full
OneBot envelope, not the AstrBot adapter's flattened data; business status/retcode required.

## Verification / release

851passed,17platform-dependent skipped,18subtests; pyflakes/compile/release scan clean.
Tests include native heartbeat read→model-selected signature→SILENT (fake edge, no public
writes), actual empty context+origin, denial of member/spoofed management, native71-tool
contracts, multi-source transcript persistence, daily cross-date wake, no-origin deferral,
chat/heartbeat lock serialization and fail-closed uncertain mutation receipts.
Real model candidate/deployed probes allow only account/status reads, never public sends,
signature mutation or Qzone publication. Actual write success not claimed without real writes.
Deployment must use committed source,baseline hashes,backup,native exact-plugin reload,
post-checks and temporary access revocation; no service restart or policy-byte edits.
