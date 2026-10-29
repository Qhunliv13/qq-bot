# Fixed offline host-dispatch diagnostic

This control isolates scheduling in the pristine QQ host **before any native binding,
engine, login, QR or session work**. It does not import application code, patch public
APIs, hook dispatch, bypass sandboxing, await readiness or block main in a wait loop.

## Contract

The only mode is `host_dispatch_offline`. The only authored entry is
[qq_host_dispatch_entry.js](../tools/qq_host_dispatch_entry.js); no CLI source,
environment override or QQ program flags are accepted. The shared runner still runs
`/opt/QQ/qq` without flags and removes the inherited prepared-phase flag.

The entry synchronously records `bootstrapEntered` and `synchronousEnd`. It registers
public `process.nextTick`, resolved Promise, `queueMicrotask`, `setImmediate`, zero
and 1000-ms timers, and guarded Electron `app.whenReady()` / `app.on('ready')`
observations. Registration throws are recorded only as fixed enums, not error text.
Every callback marks at most once. The snapshot uses fixed flags plus a unique enum
history in actual observed order; **no relative ordering of asynchronous callbacks
is assumed**. Elapsed milliseconds come directly from monotonic `hrtime`, not a
clamp. An early timer is rearmed to the original one-second deadline.

Every checkpoint synchronously writes the fixed private path
`/tmp/qq-host-dispatch-progress.json`, with a 16-KiB bound and nonce binding.
Runtime versions are restricted to the shared seven safe version fields. No account
values, titles, raw errors, paths from the runtime or arbitrary objects are retained.
The strict parser rejects duplicate/unknown keys, nonce mismatch, unknown or duplicate
events, contradictory flags/registrations, malformed versions, nonmonotonic or
out-of-bound times and premature completion.

At one second the timer writes a completed snapshot and exits using public
`app.exit(0)` (or public process exit if Electron is unavailable). Ready need not have
happened: **completed observation is not QQ ready**. If dispatch never reaches the
timer, the unchanged external 30-second watchdog retires the process. An incomplete
snapshot remains evidence, not inferred success. Native/login/session/send-receive
verification stays false.

## Shared safety boundaries

[qq_ephemeral_host_entry.py](../tools/qq_ephemeral_host_entry.py) remains the single
runner: namespace/pivot-root isolation, detached old root, nonroot UID, zero
capabilities, no-new-privileges, offline gate, private display/runtime services,
30-second child and 45-second outer bounds are unchanged. The source and manifest
are read back and hashed; all five pristine host software hashes are checked before
and after. No auxiliary native metadata or modules are staged for this mode.

The existing external sampler is reused with count-only X observation. Window roles,
titles and error-category capture are not requested by this mode. Existing modes
retain their prior behavior.

## Review and execution

[qq_host_dispatch_control.py](../tools/qq_host_dispatch_control.py) accepts only
`--qq-root` and explicit `--execute`. Before actual execution it exclusively reserves
`docs/qq-host-dispatch-evidence.json`; an existing artifact refuses another invocation
before any run. Do not delete or overwrite that evidence to retry. Parent review and
GO are required; tests alone do not authorize an actual run.

Fake-only coverage in [test_qq_host_dispatch_control.py](../tests/test_qq_host_dispatch_control.py)
executes the actual authored entry under a VM with substituted public APIs, including
variable dispatch order, duplicate callbacks, Electron registration throws and a
stalled dispatcher. It covers malformed evidence, inherited phase-flag removal,
fixed source routing, count-only sampler dispatch and exclusive artifact reservation.

Validation: 80 focused tests passed across dispatch, prepared-phase and shared
stall integration, including early timer rearming and frozen-evidence verification.
Python compilation, pyflakes and JavaScript syntax checks passed.

## Single approved actual observation

The parent approved exactly one run against the pristine lab root. The exclusive
[evidence artifact](qq-host-dispatch-evidence.json) records:

| Checkpoint | Elapsed milliseconds |
| --- | ---: |
| bootstrapEntered | 0 |
| synchronousEnd | 0 |
| nextTick | 0 |
| promiseThen | 0 |
| queueMicrotask | 0 |
| setImmediate | 397 |
| setTimeout0 | 398 |

All eight callback observations registered successfully. The 1000-ms timer,
`whenReady` and `onReady` were not observed before the 30-second watchdog. The
process was retired at 30.02 seconds with exit code -9. Observation is incomplete;
this is not QQ readiness or a proven root cause. Initial dispatch did work, while
later timer/ready completion did not occur in this isolated run.

All five pristine hashes were unchanged; source SHA-256 is
`4ab8582d780a2cbf4a1fdddc90990dc0c74d594edc8f177dfe219b2b61993a94`.
The safe runtime projection verified Electron 40.0.0 and Node 24.11.1. The 30
external count-only samples were classified predominantly waiting (345 CPU ticks,
100 ticks/second); no window role/title or raw error was collected. Native binding,
login, session and send/receive verification remain false. No repeat run was made.
