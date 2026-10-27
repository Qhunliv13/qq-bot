# Independent QQ transport rewrite — preservation-first work plan

Status: **inventory and offline acceptance foundation only; no replacement backend exists yet**.
Inventory date: 2026-10-27, Asia/Shanghai. The production transport remains unchanged.

## User requirement and non-negotiable boundary

Replace NapCat completely while retaining the same personal QQ account, existing functions,
messages, friendships, groups, files, persistent data, bot configuration and behavior.
An HTTP proxy to NapCat, renamed fork, reused NapCat loader, or unsupported-function stub
is not completion. Existing unsupported capabilities must also be reported honestly; they
must not be promoted into successfully implemented features during migration.

No production cutover is permitted on the basis of a passing HTTP mock or action-name
coverage. No existing channel, script, account store, configuration or source module is
removed. Do not launch a second authenticated QQ instance, copy live credentials or alter
the running account's state to demonstrate progress.

## Verified current boundary

The metadata-only runtime inspection found:

- Tencent QQ **3.2.34-53644**, build **53644**.
- Its current package entry is **`./loadNapCat.js`**. Keeping this entry would retain NapCat.
- NapCat **4.18.37**, OneBot **v11**; host-local action endpoint `127.0.0.1:13000`.
- AstrBot uses its `aiocqhttp` platform with a reverse WebSocket listener on port 6199.
- Two persistent mounts hold existing QQ state and transport configuration. Exact private
  paths and credentials are deliberately omitted. Neither account-state files nor cookies
  were copied or inspected by this inventory.
- The client installation has native `wrapper.node` and `major.node` libraries plus its
  Electron runtime. Their presence is **not** proof of usable exports, a compatible ABI,
  a stable API, login capability or independent event registration.

Local source inspection identifies **21 distinct currently called action names** and
seven event/data-flow categories. This is a **minimum observed bot contract**, not a
complete installed NapCat API inventory or a claim of all QQ features. The offline
manifest and audit tool use this distinction explicitly.

## Dependency map

| Boundary | Existing dependency | Independent implementation needed |
|---|---|---|
| Client startup | Package entry loads NapCat | Own bootstrap with no NapCat code or loader dependency |
| Personal-account session | QQ client's current account state | Authorized session lifecycle, status and reconnect verification; no blind credential migration |
| Native kernel | Tencent QQ installation | Version-pinned bindings and event subscriptions demonstrated in isolation |
| Action ingress | OneBot v11 HTTP actions | Own compatible request validation, dispatch, errors and read/write semantics |
| Event egress | Reverse WebSocket | Own event serialization, source identities, ordering, heartbeat and reconnect handling |
| Messages/media | Native send and upload operations | Text, mention, quote, image, voice, video, files, forward records and exact receipt behavior |
| History and identifiers | Current native IDs and source records | Preserve ID types, source scope, sender, timestamps and quote/forward lineage |
| Approvals/profile/social | Backend-specific actions | Preserve approval flags, permissions, Qzone/profile behavior and acceptance/readback distinction |
| File namespace | Explicit `docker cp` staging into `napcat` | Own file access/staging and cleanup lifecycle, without deleting uncertain-result files |
| Durable bot state | Existing bot stores | Preserve all histories, facts, tasks, goals, observations, policies and transfer ledgers |

A legacy port-3000 `get_msg` fallback and VM-side QQ endpoints also require coverage.
Changing only the primary port-13000 bridge cannot remove the full dependency.

## Proposed independent architecture

The first candidate is an independently authored bridge around the existing Tencent QQ
client runtime. Tencent's client is not NapCat; retaining a QQ client is different from
retaining NapCat. This is a **candidate architecture**, not a demonstrated implementation.
It must not import NapCat packages, load its JavaScript, call its endpoints or retain its
startup hook. Third-party source must not be copied under a claim of independent authorship.

Separate components would own:

1. Client bootstrap, version detection and capability negotiation.
2. Native QQ session and event bindings.
3. Message, media, history, group, friend, approval and social services.
4. A OneBot-compatible external contract for the existing AstrBot integration.
5. Ordered event forwarding, write-operation outcome tracking and isolated diagnostics.
6. Reversible activation and restoration without changing production account state during development.

A from-scratch QQ wire-protocol client would additionally need a verified implementation
of session authentication, signing, synchronization and media protocols. None of those
are supplied by the existing Python bot or by an HTTP compatibility server. Do not present
such a server as an independently working QQ client.

## Preservation and acceptance matrix

Every existing supported operation must be verified for:

- Exact account identity, destination, participant IDs and authorization boundaries.
- Real events and action round-trips, including quotes, forwards and non-text attachments.
- Native acknowledgements versus delivery confirmation: accepted, confirmed, failed and
  unknown remain distinct; unknown mutations are never automatically replayed.
- Disconnect/reconnect ordering, no duplicate admission or delivery, and durable operation IDs.
- Private/group isolation, request approval flags and current owner checks.
- Historical IDs, local files, UTF-8 content and original metadata, without destructive conversion.
- Existing automation, scheduling, observations, autonomous opportunities and all 106 bot
  tool schemas; schema count alone is not evidence of platform capability.
- Backup restoration and a controlled migration rehearsal with no interrupted in-flight work.

The installed backend's complete API/event inventory must be collected separately before
claiming full replacement. A new candidate must not pass by covering only the 21 actions
currently observed in bot source.

## Current implementation increment

The accompanying offline contract manifest/auditor establishes a no-silent-drop baseline.
It can detect missing declared actions and event categories in a proposed candidate.
A candidate declaration is not trusted end-to-end evidence. Passing declaration coverage
**never authorizes production cutover** and never claims that QQ login, native bindings,
network traffic or lossless migration has been implemented.

## Next validation gates

1. Complete the installed backend API/event inventory and preserve configuration mappings.
2. Obtain an isolated, version-matched QQ software environment without live account data.
3. Prove independent native bootstrap and event binding without NapCat loading.
4. Only with separately authorized test-account access, verify login and actual action/event
   behavior. Do not assume production-account use is acceptable for an experiment.
5. Complete feature/data parity tests and rollback rehearsal before proposing a production switch.

Until these gates pass, leave the existing NapCat instance, its mounts and running QQ account
untouched. No completion date or all-feature equivalence is asserted by this initial work.
