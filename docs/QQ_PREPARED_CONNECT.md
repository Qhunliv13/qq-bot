# Prepared-host single offline connect experiment

## Scope and authorization

This fixed experiment reuses `qq_transport/native_host.js`, `native_login.js` and
`native_metadata.js`; no factory lifecycle is duplicated. It does not switch the
production QQ process, copy account/login state, create credentials, request a QR,
authenticate, initialize/start a session or send messages. Native loading remains
inside the existing disposable shared runner, never the ordinary test interpreter.
Default invocation does not execute QQ; actual execution requires explicit owner
approval and `--execute`. One approved attempt is not permission to retry.

The only fixed mode is `prepared_connect_offline`. Sources are the authored entry,
three reusable modules and pristine package metadata. The shared runner accepts
exactly this mode's four auxiliary paths; all other modes retain their original
exact staging set. No caller-supplied script, native method, account or network
option is accepted.

## Exact native boundary

1. One `process.dlopen` of the pristine wrapper in its matching QQ executable.
2. `createNativeHost({wrapper, sessionName: 'nt_1', lifecycleMode: 'startup'})`, then
   `host.prepare()` once: engine get, login get, startup create, session get.
3. The login controller receives `host.getLoginWrapper()`: native gets are not
   repeated. Resolve the existing pinned QQ 3.2.34-53644 metadata unchanged.
4. Initialize once with explicit process-owned listener lifetime. Only the already
   observed `undefined` registration return proceeds; an unexpected numeric handle
   fails closed and is retired by process exit, without invoking native removal.
5. Connect exactly once. No retry, alternate signature, QR, auth or session action.
6. Observe fixed JS timer deadlines at 1, 5 and 8 seconds after connect returned.
7. For the measured void listener, logically dispose login and host, write the
   final record, and request public Electron process exit. No native teardown.

`ok` and `native_connect_observation_verified` mean the bounded **observation**
completed with a valid final envelope and exit 0. They do not mean connect returned
true: a thrown/rejected connect can still be successfully observed. Only native
callback counters prove callback delivery, and even delivery is not authentication.

## Isolation and hard limits

The single shared implementation retains separate network/mount/PID/IPC namespaces,
pivot-root with oldroot detached, private X/dbus/runtime services, fresh account-free
config, read-only pristine software, UID/GID 65534, zero effective capabilities and
NoNewPrivs. Before launching QQ it requires only DOWN loopback, no IPv4 routes and
no forwarding IPv6 routes. QQ receives no flags and no ELECTRON_RUN_AS_NODE.

The 30-second QQ watchdog and 45-second outer watchdog remain unchanged.
`RLIMIT_FSIZE=16384` remains applicable to **every** writable file, not merely JSON
output. A native log/state file reaching that bound can terminate QQ; do not infer
deadlock from a missing timer or silently relax the limit.

## Evidence rules

The synchronous progress file has an exact schema, fresh nonce, a compact complete
prefix of fixed stage/time labels, the current safe host snapshot, and the current
safe login snapshot. No raw exceptions, arguments, handles, config, identity, QR
or native output are serialized. Each write is at most 16384 bytes. The validator
rejects duplicate/unknown keys, invalid types, skipped/reordered stages, regressing
timestamps, early timer claims, unsafe snapshots and lifecycle contradictions.
A torn/invalid write is missing evidence, not inferred progress.

Stages: runtime, before/after binding load, before/after prepare, before/after
initialize, before/after connect, tick 1/5/8 seconds, before/after dispose.
Preparation uses the module as-is; this experiment does not instrument its native
factories individually. A stalled prepare therefore identifies only that boundary.

- `after_prepare`: the reusable actual-host prerequisite returned.
- `after_connect`: connect returned or threw, described by the actual safe snapshot.
- `tick_8s`: bounded JS timer dispatch was observed, not transport readiness.
- Final validated record plus exit 0: the observation completed.
- Watchdog/missing final: incomplete; retain last validated prefix only. Not proof
  of deadlock, callback success, login readiness or production suitability.

The runner reports the authored source SHA256 and all four auxiliary SHA256s after
exact staged-byte readback. Existing five host-software hashes before/after must
match. Hashes attribute the software/evidence only, never prove native success.
Login, session, full send/receive, independent readiness and production-cutover
flags remain false. Native listener removal is never claimed.

## Offline tests

```text
python -m pytest tests/test_qq_prepared_connect_control.py tests/test_qq_ephemeral_host_entry.py tests/test_qq_native_connect_control.py tests/test_qq_native_factory_control.py -q
node --check tools/qq_prepared_connect_entry.js
python -m pyflakes tools/qq_prepared_connect_control.py tools/qq_ephemeral_host_entry.py tests/test_qq_prepared_connect_control.py
```

Tests inject fakes into the authored entry while importing the real reusable host,
login and metadata modules. They exercise once-only order, thrown preparation and
connect, numeric-listener refusal, deadlines, strict parser mutations and unchanged
old-mode isolation routing. These tests do not execute QQ or prove native delivery.
Actual evidence, if approved, is reported separately; no actual result is assumed.

## 2026-10-29 execution-capture limitation

The approved launch command's PowerShell pipeline failed while binding
`Out-File -Encoding utf8NoBOM`: the actual shell accepted only the older encoding
set. No evidence file or native JSON result was produced and no background job
was returned. A subsequent read-only process check found no surviving QQ, Python,
unshare or observation process. This does not establish whether upstream native
execution occurred before the capture failure, so the outcome remains **unknown**,
not successful, not a measured watchdog stall, and not proof of reusable-host
native behavior. The owner explicitly prohibited retrying this round. No fake
native evidence artifact was created. Focused fake-only validation passed 95 tests;
it remains separate from the unavailable actual-native observation.

## Subsequent single isolated observation (2026-10-29)

A later goal round used the reviewed PS5-compatible capture script for one new
bounded attempt against source commit `d159840`. The immutable sanitized
[observation](qq-prepared-connect-evidence.json) records:

- Actual QQ invoked in the shared private namespaces, UID 65534, zero capabilities,
  NoNewPrivs, detached oldroot, DOWN-only loopback and no forwarding routes.
- Binding returned at 36 ms, prepared host at 38 ms, initialization at 47 ms,
  and one boolean-returning connect call at 47 ms. The boolean value is deliberately
  not serialized; phase `connecting` with no failure is not a connected callback.
- The final valid checkpoint is `after_connect`. No 1/5/8-second timer checkpoint
  was observed. All seven recorded native login callback counters remain zero.
- QQ retired under the unchanged 30-second watchdog (`exit_code=-9`, elapsed
  30.13 s). No final envelope exists: status remains failed, bounded observation
  incomplete, authentication/session/send-receive unverified.
- All five pristine software hashes match before/after. Authored entry and staged
  module hashes are in the artifact. It attributes the **pre-login-retirement-fix**
  module at `d159840`; later source fixes have not been re-tested in actual QQ.

This disproves the narrow expectation that preparing startup/session factories
alone would make the fixed offline timers progress in this run. It does not prove
why progress stopped, a deadlock, an ABI mismatch or a network-dependent cause.
Missing final records leave older summary flags such as `runtime_versions_verified`
false; the progress record does not contain version strings, so none are invented.
The process-wide 16-KiB regular-file limit and offline environment remain unchanged.
No production account, session, configuration or transport was accessed or changed.
