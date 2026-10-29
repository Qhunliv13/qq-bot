# Fixed native file-size contrast

This is an **explicitly changed resource profile**, not a pass under the old
16KiB profile. It follows the logging-enabled offline host-dispatch control and
the actual Linux tmpfs/memfd `ftruncate(32768)` contrast (EFBIG at16KiB, success
at64KiB). Absence of a stderr `file_size_limit` token does not exclude EFBIG.

## Single changed variable

`tools/qq_host_fsize_control.py` selects only `host_dispatch_fsize_offline`:
QQ child RLIMIT_FSIZE soft/hard **1048576 bytes**, instead of16384. No arbitrary
limit CLI exists. Shared runtime supervisor already sets its soft/hard FSIZE to
1048576; the control verifies those actual limits before Popen and never raises
the supervisor cap. Other modes retain exactly `preexec_fn=child_limits` and
16KiB. The new mode uses a partial of that single validated implementation.

Logging remains `ELECTRON_ENABLE_LOGGING=1`. Source SHA256 remains
`4ab8582d780a2cbf4a1fdddc90990dc0c74d594edc8f177dfe219b2b61993a94`.
Synthetic identity, wait/exit/resource sampler, CPU30s, NOFILE128, core0,
16MiB tmpfs, UID/GID65534, capabilities0, NNP, network/mount/PID/IPC isolation,
sandbox, read-only software and permitted private writable locations are
unchanged. No accounts, login, network access, hooks, GPU changes, sandbox flags,
explicit native binding loads, or vendor preload changes are introduced.

## Accurate evidence boundaries

The public `experimental_resource_profile` contains expected and **measured**
QQ numeric limits from identity-verified main-process samples. Missing samples
are unknown, not expected values substituted as measurements. The new mode
removes the old `resource_bounds_unchanged` field; old reports are untouched.
Supervisor numeric FSIZE is measured separately. Full stdout/stderr file sizes
are reported, but private capture remains **only the first16384 bytes each**.
`prefix_capture=true`, `capture_limit_bytes=16384`, prefix-full flags, and
separate 1MiB file-limit flags distinguish capture bounds from OS limits.
A false diagnostic token is not evidence that an OS operation succeeded.

The private reader, base64 envelope, strict parser, and outer128KiB helper report
limit remain unchanged. Public evidence has hashes/lengths/categories, never raw
application bytes. Generic CLI requires an external private path and reserves
public evidence exclusively at `docs/qq-host-fsize-evidence.json`.

The reviewed external wrapper writes Linux `/tmp/qq-host-fsize-private-20261030.json`,
verifies owner/mode0600, then exports at most300KiB to the external Windows
`_inbox/qq_host_fsize_private_20261030.json`. It makes no POSIX-mode claim for
DrvFS. Destinations are exclusive and cannot silently overwrite prior evidence.

## Execution discipline and interpretation

Focused fake-only tests precede source freeze and parent review GO. Exactly one
actual run is permitted. If1MiB is insufficient, preserve that actual result;
no further relaxation or native run follows. Successful host dispatch or Electron
ready is not native binding, login, or send/receive success. The unchanged entry
has no explicit binding load, and vendor preload is an independent concern.

## Closed actual result (2026-10-30)

Exactly one parent-approved run returned `observed`, errors empty, exit0,
`application_exited`, **1.28s**. All10 dispatch flags are true: `onReady` and
`whenReady` at332ms; `setTimeout1000` at1001ms. The logging-enabled16KiB
baseline instead reached the30s watchdog, exit-9, with incomplete dispatch.

Both identity-verified main samples measured FSIZE soft/hard1048576,
CPU soft/hard30 and NOFILE soft/hard128. Actual supervisor FSIZE was also
1048576. Source hash and the five nominated software hashes are unchanged
before/after and match the logging baseline; isolation and invocation match.
This single controlled trial supports the restrictive child file-size profile
as a decisive contributor to the observed host startup failure, consistent with
the earlier actual ftruncate EFBIG contrast. The conclusion is limited to this
control condition and1.28s observation; it is not a long-running stability test
and does not imply that remaining shared-memory or system-bus warnings are
always harmless. It does not identify an exact native syscall stack or the
precise failing allocation/instruction from this run.

Full stdout156B/stderr818B were captured within the16KiB prefixes; verified
Linux0600 private export2354B. The stderr `file_size_limit=false` token remains
irrelevant to detecting an unlogged EFBIG; measured numeric limits and the
controlled startup contrast are the evidence. Native binding, host binding load,
login, send/receive, independent backend readiness, and safe-to-login all remain
**false/unverified**. This is host dispatch/ready only, not a native QQ login pass.
