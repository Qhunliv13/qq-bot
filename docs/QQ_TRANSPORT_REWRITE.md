# Independent QQ transport rewrite — preservation-first work plan

Status: **static native investigation and isolated transport core; no replacement QQ backend exists yet**.
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
complete installed NapCat API inventory or a claim of all QQ features.

A subsequent read-only inspection of the installed 4.18.37 software bundle resolved
**180 unique base actions**, plus **360 generated `_async` / `_rate_limited` aliases**,
for **540 lookup keys**. Aliases use the same handlers; they are not 360 additional
features. The 181 registration entries include one repeated `check_url_safely` entry.
All 21 plugin action names are an exact subset of the 180 base names. The snapshot,
bundle digest and registration provenance are in `qq-installed-action-inventory.json`.
This is constructor/decorator **static** coverage, not verified runtime registration,
complete event coverage or successful QQ functionality.

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

For the broader installed action baseline, run:

```bash
python tools/qq_transport_parity_audit.py --installed-inventory docs/qq-installed-action-inventory.json
```

Add `--candidate /path/to/candidate.json` to check a candidate declaration. Merely covering
the 21 bot actions does not pass the 540-key installed-action check. Even a complete
self-declaration leaves native implementation, runtime verification and cutover false.

The independent offline preflight is:

```bash
python tools/qq_native_preflight.py --qq-root /path/to/isolated/QQ
```

It reads only bounded software metadata, the bootstrap entry literal and 64-byte native
library headers. It does not import libraries, start QQ/Electron, read account-state
folders or access the network. ELF architecture is not ABI compatibility. Exit **1**
is intentional for every static preflight result: login and cutover are never authorized.

A local WSL x86_64 environment with `readelf` and `dpkg-deb` was verified as available;
this alone is not a QQ runtime lab. Official Tencent distribution metadata advertises
base version 3.2.34 but does not establish build 53644 equivalence. Package download
metadata was refused with HTTP 403 in the initial check; no installation was performed.
Official software binaries, if staged for static analysis, stay outside the repository
and contain no copied account data.

## Isolated transport core increment

`qq_transport/` now provides independently authored, offline-testable operation
journaling, injected action dispatch and a durable source-event outbox/pump. It has
no NapCat imports, endpoint fallback or copied handlers. No default native handlers
are installed and importing it starts no service. **Zero real QQ native handlers are
implemented or verified.** See `qq_transport/README.md` for the exact contract.

Mutations are claimed durably before invocation; pending/unknown claims cannot be
automatically retried. Complete responses are immutable and replayable. Events retain
source fields and account separation and are acknowledged in insertion order. A crash
after remote acceptance but before local acknowledgement can replay the same event ID:
this is at-least-once delivery, not an exactly-once network guarantee. Application-level
acknowledgement for the existing AstrBot reverse WebSocket remains unimplemented.

The internal mutation interface requires an operation ID and therefore is not yet a
drop-in OneBot compatibility layer. HTTP/WS serving, authentication, native QQ binding,
real login, event subscription and lossless migration remain future work. No production
adapter or configuration has been changed.

## Static event and message baseline

`qq-installed-event-inventory.json` is tied by byte size and SHA-256 to the same
installed 4.18.37 bundle as the action inventory. It records **32 discriminant-bearing
class candidates**, not 32 distinct event types: candidates include base classes and
similarly named non-event fields. Inheritance, dynamic values and export namespace
names are unresolved. Lifecycle, heartbeat and group-upload literal discriminants
have source-line evidence; runtime emission and schema requiredness do not.

The bundle also has **23 string-enum message type values**, including voice's `record`
value, nested-forward/node, images, files and flash-transfer. Enum presence does not
prove that every segment is accepted by every send action or emitted on receive.
The local member-list-delta flow remains explicitly non-native. Full event parity is
still unknown; these facts must not be converted into automatically enabled handlers.

## Static native feasibility findings (2026-10-27)

The native software boundary was inspected without loading libraries or starting QQ:

- `qq-native-abi-evidence.json` records complete remote `readelf` output provenance.
  `wrapper.node` imports `qq_magic_napi_register`, and the shipped QQ executable
  exports it. The wrapper also imports 41 `napi_*` symbols. This is evidence of
  QQ host coupling, not an ordinary generic-Node addon implementation. No conventional
  Node/N-API registration entry was observed in that symbol table; this is not an
  absence claim about proprietary initialization or relocation mechanisms.
- `qq-native-bootstrap-evidence.json` records that two official outer launchers
  reference `../major.node`. The existing package main is NapCat-modified. The
  archive has no root `package.json`, so an unmodified original main was not recovered.
  Sampled archive payloads were not readable UTF-8; no encryption or missing-API
  conclusion is inferred from that.
- `tools/qq_asar_inventory.py` is independently authored, header-only ASAR metadata
  inspection. It does not extract/read entry payloads or execute code. Synthetic
  tests verify bounded reads, path handling, offsets and malformed input rejection.
- The official-listed package endpoint refused both initial HEAD and a single bounded,
  anonymous Range GET with HTTP 403 from this environment. This does not prove that
  the package does not exist or is unavailable everywhere. No mirror, authentication
  bypass or QQ account credentials were used.

The preceding 403 observations apply to the public-release URL checked in that phase;
a later exact build URL succeeded, as recorded below. **Practical gate:** establish
the actual official host/bootstrap prerequisites before a runtime experiment. Do not guess an API from exported C++ names, call a native constructor in
the live process, use generic Node `require` as a parity demonstration, or retain
NapCat's loader under a new name. Login and all 180 base actions remain unimplemented.

## Pristine package recovery (2026-10-27)

`qq-pristine-package-evidence.json` records a complete **187,185,496-byte** download
from the exact public Tencent-hosted build URL explicitly present in the upstream
publisher Dockerfile. An ordinary anonymous range request returned HTTP 206; the
package was saved only outside the repository. No installation scripts, native library
or QQ executable were run. Package-control metadata confirms `linuxqq 3.2.34-53644`.

The original 2,156-byte QQ manifest declares:

```text
./application.asar/app_launcher/index.js
```

Its MD5 `0c940cb5624561345e6a8dbe9a713b3e` matches the installed DPKG original-file
record, unlike the currently modified NapCat manifest. SHA-256 is recorded for the
package and manifest; no independent Tencent-published checksum was available. The
MD5 match corroborates package metadata, not publisher authenticity or equality of
every installed application byte. The original entry is now established, superseding
older snapshots where it was unknown. Native initialization and JS APIs remain unknown.

Unverified child claims about DockerHub index/architecture/layer mappings were
explicitly retracted and are not evidence. Container/image GraphDriver metadata was
actually null; no lower-layer mount, image rebuild or production modification was used.

The static preflight must treat `application.asar/app_launcher/index.js` as an archived
entry, not an ordinary host file. Archive entry existence is checked through the
single header-only ASAR implementation; it does not read the entry's payload or prove
that it can be executed, and all login/cutover readiness flags remain false.
An actual local run against the pristine manifest and 30,361,684-byte archive found
`app_launcher/index.js`, an 80-byte packed entry, with no metadata errors. Only these
two software files were staged: native libraries and executable were not copied into
this static lab. The intentional non-ready exit code remained 1.

## Next validation gates

1. Complete the installed backend API/event inventory and preserve configuration mappings.
2. Safely stage the now-downloaded matching-version QQ software in an isolated environment without live account data; downloading a DEB is not an initialized runtime.
3. Prove independent native bootstrap and event binding without NapCat loading.
4. Only with separately authorized test-account access, verify login and actual action/event
   behavior. Do not assume production-account use is acceptable for an experiment.
5. Complete feature/data parity tests and rollback rehearsal before proposing a production switch.

Until these gates pass, leave the existing NapCat instance, its mounts and running QQ account
untouched. No completion date or all-feature equivalence is asserted by this initial work.
