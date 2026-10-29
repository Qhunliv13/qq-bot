# Ready-first offline native diagnostic

## Scope and source boundary

This is a single authorized diagnostic, not a transport implementation or a production cutover. The fixed `connect_ready_offline` mode reuses the existing ephemeral runner, isolation gates, 30-second QQ watchdog and 45-second outer watchdog. It introduces no alternate staging jail, user-selectable script, account input, QR request, vendor bootstrap, native teardown, permission change, fuse change, or sandbox flag.

The official Electron app API was retrieved and checked by the parent before implementation on 2026-10-29: https://www.electronjs.org/docs/latest/api/app . Its `ready` section says readiness follows Electron initialization and the first main-process event-loop tick; `app.whenReady()` resolves upon initialization, and `app.isReady()` returns a boolean. Those lifecycle semantics motivated the control; documentation is not evidence of this runtime's observed outcome.

## Fixed ordering

The authored main requires only standard `fs`, `os`, and Electron app APIs before readiness. It synchronously writes `before_when_ready`, queues one pure zero-delay timer, and awaits `app.whenReady()`. Only after the synchronous `after_when_ready` checkpoint may it load the wrapper, metadata/controller modules, initialize the process-lifetime policy and call connect exactly once. Subsequent fixed checkpoints are `before_initialize`, `before_connect`, `after_connect`, `after_wait`. Every checkpoint includes actual `app.isReady()` and pure-timer-fired booleans. Snapshots are null until an applicable controller milestone exists; there is no synthetic native snapshot.

The zero-delay timer refreshes the current fixed checkpoint with the actual timer fact without inventing a stage. A fixed-category uncaught-exception/unhandled-rejection handler exits instead of permitting an Electron default error dialog to obscure a failure. It records no exception text, argument, stack, identity, image, or credential. Standard `app.exit` is the only authored exit API; no native shutdown function is invoked. If readiness never resolves, no native entry is reached and the existing external watchdog retires the process.

## One actual result (2026-10-29)

Invocation: WSL distribution `Ubuntu-TSC`, root, `python3 -m tools.qq_native_ready_control --qq-root /tmp/qq-native-lab-20261027-pristine/opt/QQ --execute` from the checked-out project.

Exactly one ready diagnostic was run; no fallback or second native experiment was performed.

- Completion: `watchdog_30s`, elapsed **30.01 seconds**, process exit **-9**, process retired.
- Last actual checkpoint: **`before_when_ready`**.
- Actual `app_is_ready`: **false**; pre-wrapper `pure_timer_fired`: **true**; snapshot: **null**.
- No `after_when_ready` was reached. Wrapper load, initialize and connect therefore were **not reached** by this authored program.
- Host result file absent: `host_record=null`, status `failed`, error `custom_main_no_valid_record`. This is not a validated native result. No synthetic initialization envelope was promoted into evidence.
- Fixed fatal event file absent: `ready_fatal=null`; no claim about an unobserved exception is made.
- The actual private-network gate proved only DOWN loopback, no IPv4 routes, and no IPv6 forwarding routes. Network/mount/PID/IPC/pivot-root/detached-oldroot/unprivileged/no-new-privileges/zero-effective-capability gates remained true.
- The QQ executable, package manifest, wrapper, application archive, and original launcher all have identical before/after SHA-256 values (five software files).

The safe raw projection is in `qq-native-ready-evidence.json`. It contains the actual authored-main hash and staged module hashes. The saved authored main hash is `2749a469a8059c8276c0f3129accf7c65eea6e384db5eeab2a5ea48579033b71`.

## Grounded comparison, not an ABI theory

The previous `connect_offline` run reached `after_connect`, recorded a returned boolean, all native callback counts zero, and timed out before its eight-second wait completed. This ready-first control instead stops before any wrapper/native call while its pure JS timer fires. It establishes an unresolved **Electron application readiness/custom-main lifecycle in this harness** independently of wrapper loading, and rules out blaming native connect as the cause of this control's pre-native stall. It does not establish the underlying lifecycle cause, nor prove that all JS timers stall, nor identify the native ABI as incompatible. The prior observation remains valid but cannot alone isolate its cause.

Transport/login/session/send-receive readiness, ABI compatibility, `safe_to_login`, independent-backend readiness and production cutover remain unverified/false. There was no home/session/credential access or production-network operation. No additional attempt is authorized by this report.

## Validation

The explicit ready parser extends only its own fixed stage enum; the old connect parser is unchanged and still rejects the additional fields/stages. Connect and initialize result/snapshot validation are reused through parser-only envelopes. All Python helpers are copied and imported before pivot detaches their staged paths.

Final focused fixed-control/native-ownership regressions: **200 passed in 3.29 seconds** (including 12 ready-specific tests). Pyflakes, Python compile checks and `node --check` pass. The authored-main SHA is checked against actual evidence, and all owned files are checked as UTF-8 without BOM. The full project suite collected the original 11 ready tests and completed with **3534 passed, 38 skipped, 111 subtests passed in 184.64 seconds**; the final additional honest-refusal test was then included in the 200-test focused run above. No native invocation is performed by these tests.
