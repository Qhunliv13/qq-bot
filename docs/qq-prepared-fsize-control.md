# Prepared-native successor on verified host profile

This fixed `prepared_connect_fsize_offline` experiment reuses the unchanged
[prepared entry](../tools/qq_prepared_connect_entry.js),
[strict prepared parser](../tools/qq_prepared_connect_control.py), and native host,
login and metadata modules. It does not introduce a new native implementation.
The existing prepared entry prepares factories, initializes engine/login configuration,
registers an inert listener, calls connect once, waits for its existing 1/5/8-second
ticks, logically disposes and retires the process. It requests no QR, authentication,
account login, session initialization, or IPC messages. Factory availability is not
session initialization; a true connect return is not a connected callback.

## Comparison boundary

This is **successor_verified_host_profile**, a multi-variable comparison against
old prepared: synthetic private identity, `ELECTRON_ENABLE_LOGGING=1`, and QQ
RLIMIT_FSIZE=1MiB change together. The earlier host FSIZE success (authored entry makes no explicit native calls;
independent vendor preload remains possible) is only profile evidence, not native readiness.
The current entry hash matches the later prepared-phase evidence exactly; historical
prepared/stall evidence used an earlier entry before phase-support was added. This run
leaves the phase flag absent, retaining the existing non-phase path. Source identity
is asserted against current HEAD and prepared-phase evidence, not the older stall bytes. No single-variable claim is made
against old prepared. The prepared phase flag remains absent.

The same privilege drop, CPU=30s, NOFILE=128, core=0, 16MiB private tmpfs,
private display/bus, UID65534, zero capabilities, no-new-privileges and no-network
checks remain. The supervisor must measure exactly 1MiB soft/hard FSIZE before
launch; the existing child preexec is partially bound, not reimplemented.
Only the new mode requests detailed resource/wait/exit sampling. Old prepared
retains its exact waiter kwargs and basic capture. Final/progress parsing stays
prepared-native, never the host dispatch parser. `observed` means completion of
bounded diagnostic observation, not successful connectivity, login, or readiness.

## Consent and artifacts

[Fixed CLI](../tools/qq_prepared_fsize_control.py) accepts only `--qq-root`,
`--execute`, and `--private-output`. Execute requires an external private destination;
the public evidence is exclusively reserved and cannot be overwritten.
The reviewed external driver reuses the existing exporter: Linux private output
must be a regular owned 0600 file, external Windows export is bounded at 300KiB,
and no POSIX-mode claim is made for DrvFS. Raw output remains private, limited to
16KiB per stream; only lengths, hashes, bounded categories and validated snapshots
are public. The helper envelope remains bounded at 128KiB.

One actual requires parent GO; failures are evidence, not permission to retry.
No production, account, repository commit or full-suite execution by the child.

## One approved actual observation (2026-10-30)

[Public evidence](evidence/qq-prepared-fsize-evidence.json): status `observed`, exit 0,
`application_exited` in 8.33s, no watchdog. Prepare completed at 35ms, initialization
at 43ms, connect returned at 44ms, existing ticks at 1045/5046/8045ms, and logical
disposal completed at 8045ms. Native callback counts: connecting 6, disconnected 5,
connected 0; final phase is `disconnected`. All QR/authentication callback counts
are zero; login/session/send-receive/readiness remain false. This verifies diagnostic
callback/timer dispatch under the successor profile, not network connectivity.

Nine measured samples match CPU30/NOFILE128/FSIZE1048576. Private export is 4504 bytes,
with Linux owner/0600 verification; no raw output is included here. Source and staged
module hashes match prepared-phase evidence, host software hashes are unchanged.
No second actual was attempted.
