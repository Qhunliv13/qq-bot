# External vendor stall observation

## Scope and execution

One authorized isolated execution was performed on 2026-10-29 using WSL root,
`/tmp/qq-native-lab-20261027-pristine/opt/QQ`, from baseline `5bdc8a0`.
The report is `docs/evidence/qq-vendor-stall-evidence.json`. No second actual execution was
performed. Subsequent validation uses fakes/pure projections only.

```text
python3 tools/qq_vendor_stall_observation.py --qq-root /tmp/qq-native-lab-20261027-pristine/opt/QQ --execute
```

The only new trusted control mode is `vendor_stall`. It uses the common
`qq_ephemeral_host_entry` staging, nonce parsers, read-only overlays, private PID /
mount / IPC / network namespaces, detached old root, private authenticated X,
private D-Bus/fontconfig, and watchdog cleanup. The exact existing authored
`qq_vendor_bootstrap_entry.js` is reused unchanged, SHA256
`df94643e4b0577a1aa4d0e5e43b1d4821ff2a0e9e2a1928d479115e5625a35f4`.
No QQ flags, native calls, native/runtime bytes, production files, account data,
login, network access, or arbitrary CLI scripts were introduced. Existing default
controls retain their prior wait and diagnostic classifier.

## External observer

`observe_wait(process, env, timeout=30, report=...)` is called only for the new
mode. Its module and the existing application sampler are imported before pivot.
It reuses the existing bounded trusted unprivileged fork sampler by installing a
fixed local callback and restoring it in `finally`; it does not duplicate the
runner, privilege hook, Xlib API, or subprocess watchdog implementation.

Sampling is scheduled once per second with a fixed maximum of 30 records and a
0.75-second sampler ceiling. Each record checks the exact QQ executable,
UID65534, cap-zero, NNP and user namespace via the existing process identity
implementation. It compares PID/starttime across samples and revalidates identity
before/after each sample. Only the main QQ PID is selected for CPU/thread sampling;
only the first 64 TIDs are inspected. `/proc/PID/stat` CPU ticks are process-wide
CPU, not main-thread-only CPU. State/thread count and first/last CPU ticks are
projected; raw comm and raw wchan symbols are discarded. Wchan output is only one
of `futex`, `poll`, `wait`, `read`, `other`. `other` includes unavailable/redacted
zero and is not a known wait reason. No schedstat or thread stack is read.

X sampling queries only the private tree and `_NET_WM_PID` using existing Xlib
functions. Output contains counts, not window IDs, titles, classes, text, images,
or arbitrary property values. Main-PID correlations require matching starttime.
Window roles were deliberately **not queried**: `dialog_presence: null` means
unknown, not false. No X actions, clicks, or input are sent.

No process memory, fd contents, maps, environment, command line, stack, session,
or account IDs are read by this module. Existing identity checks read only fixed
proc stat/status/exe/user-namespace metadata inside the private PID scope.
The observer reads only fixed numeric resource-limit projections and `/tmp`
filesystem free bytes, never `/tmp` directory contents. Bounded private stderr is
classified into fixed booleans only, without raw capture in the report; stdout
and stderr sizes are obtained by fstat before private streams are cleaned up.

## One actual result

- 30 identity-verified samples; elapsed 30.06 seconds; completion `watchdog_30s`.
- Bootstrap returned, Electron ready callback observed, `app.isReady()` true.
- The 0ms timer was recorded before ready; 5s/8s checkpoints were **unobserved**.
  Missing checkpoints do not prove timers never executed: later progress writes
  could also fail. No nonce-valid final 8s record or fatal record was obtained.
- Process CPU rose from 0 user/0 system ticks to 34 user/303 system ticks at
  100 ticks/second: 337 aggregate ticks (~3.37 CPU seconds) over ~29 seconds.
  Final thread count was 32, without 64-thread truncation. Main state was `I`
  in late samples; thread snapshots were mostly `I`, with occasional `R`.
- Summary `predominantly_waiting_observed` means predominantly sampled sleeping
  states and low process CPU, not a root cause or deadlock diagnosis. Wchan was
  `other` throughout: no specific futex/poll/read explanation is established.
- Final X sample visited four private windows and correlated two with the main
  QQ PID. Window roles/modal/dialog state and UI contents remain unknown.
- Stderr was 1,950 bytes, stdout 156 bytes, neither prefix truncated. Only the
  new `dbus_unavailable` category matched. The private D-Bus service nevertheless
  passed its readiness/socket checks: the message alone does not prove the
  private session bus failed or that D-Bus causes the stall.
- `too_many_open_files`, `file_size_limit`, `fontconfig_unavailable`, `sandbox`,
  and `sharedmemory` did not match the bounded stderr prefix. Nonmatches are not
  proof that no relevant file, descriptor, sandbox, or shared-memory failure
  occurred elsewhere.
- Runtime limits were independently observed as soft/hard FSIZE=16,384 bytes,
  NOFILE=128 and CPU=30 seconds. **FSIZE applies to all regular files**, not just
  captured stdout/stderr; private cache/config/log writes may therefore be
  affected without producing a captured category. Limits were not raised.
- `/tmp` free bytes fell from 16,748,544 to 16,351,232; this is a bounded filesystem
  availability fact, not a breakdown of application files or proof of capacity
  sufficiency for every operation.
- Exit `-9` is the supervisor's deliberate retirement after the 30-second
  deadline, not a naturally observed application crash. The outer 45-second
  watchdog and namespace/process-group cleanup remain intact.
- Host software before/after hashes match. No backend/native login/send-receive
  readiness is claimed. Overall legacy vendor completion remains failed because
  the final timer8s record is missing, despite the external diagnostic succeeding.

## Validation and limitations

`tests/test_qq_vendor_stall_observation.py` covers fixed shared source/default
modes, bounded reads, stat projection, wchan allowlist, cautious CPU summary,
fixed stderr categories/fstat sizes, 30-record waiter bound and restoration,
PID-reuse refusal, and forbidden read/title/action hook absence.

Post-capture changes only flush the Python-side output wrapper before fstat,
correct CLI failure exit status, and repair a test mock. They did not change
observed QQ code, runtime limits, scheduling, isolation, or the captured record.
This report is a single environment-specific observation, not a causal proof.
Potential next experiments require a separate authorization; no limit changes,
additional native calls, or further real executions are included in this task.
