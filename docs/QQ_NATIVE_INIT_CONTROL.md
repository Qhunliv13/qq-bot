# Independent QQ native initialize-only control

## Scope and authorization

This is an isolated, explicitly authorized local software experiment, not a login,
transport, account, session, production-readiness, or cutover test. Production files,
accounts, credentials, existing state directories, and network login are not read or
used. No package installation or fuse modification occurs.

The entrypoint is `python tools/qq_native_init_control.py --qq-root <pristine-root>
--execute`. Without `--execute`, it validates software metadata/hashes but does not
launch QQ. Root is required for isolated execution. The new Python control delegates
only to `qq_ephemeral_host_entry.observe(..., control_mode='initialize')`. The default
`wrapper` mode and existing wrapper CLI remain load-only. The shared internal child
argument accepts only `wrapper|initialize`; no script/source-path argument exists.

## Shared isolation, staging and provenance

No second runner is implemented. The shared runner retains private network, mount,
PID and IPC namespaces, root pivot plus old-root detach, private authenticated X11
with TCP disabled, private nonactivating D-Bus, read-only software, uid 65534,
no-new-privileges, zero effective capabilities, and 30-second QQ/45-second outer
watchdogs. The original executable is invoked as exactly `/opt/QQ/qq`, with no
`ELECTRON_RUN_AS_NODE`, inspector, loader, `NODE_OPTIONS`, or sandbox-bypass flags.

Only after the verified isolated PID1/root gate, the fixed authored JS entry, actual
reusable `qq_transport/native_login.js`, `qq_transport/native_metadata.js`, and actual
pristine package JSON are staged root-owned, mode 0444, beneath private `/tmp`.
The existing `./app_launcher/index.js` and package main are overlaid read-only only
inside the jail. `/tmp/config/qq-native-init` is freshly created mode 0700 owned by
uid 65534; it is not a copy of any account directory. Source SHA-256 and the five
pristine host software SHA-256 values are included in the evidence. Host values
must match before/after; an observed mismatch fails the experiment.

Version metadata is explicitly pinned to the verified consumer revision `ba0a53d`
public software table: version `3.2.34-53644`, arch `x64`, numeric appid `537391664`,
QUA `V1_LNX_NQ_3.2.34_53644_GW_B`. The resolver receives the actual unmodified
pristine package JSON, explicit Linux/x64, actual `os.release()`, fixed lab hostname
`qq-isolated-lab`, and the fresh private data path. No account metadata is supplied.
The source table is metadata provenance, not an upstream loader execution path.

## Allowed native lifecycle

The entry loads the wrapper with the existing builtin `process.dlopen` consumer
call, creates the reusable controller, calls `initialize()`, then only after success
captures the real `getSnapshot()` and calls `dispose()`. The control explicitly
selects `listenerLifetime: 'process'` because this native add call returns void.
If a numeric handle exists, disposal removes only that exact ID; without a handle,
disposal only clears controller state and native cleanup requires child process
exit. The default reusable-controller policy remains token-required and fails
closed on void; this opt-in is not an inferred compatibility fallback. It never calls `connect()`, `getQRCodePicture()`, password
login, session getters, or guessed native shutdown/offline/destroy methods. Public
Electron process exit and namespace process cleanup terminate native threads.

The explicit inert GlobalAdapter contains only `onLog`, `onGetSrvCalTime`,
`onShowErrUITips`, `fixPicImgType`, `getAppSetting`, `onInstallFinished`,
`onUpdateGeneralFlag`, and `onGetOfflineMsg`. It does not synthesize dispatcher
success. The reusable controller validates wrapper exports and static get methods
as own data properties, and resolves native instance methods through bounded,
accessor-safe prototype traversal.

A nonce-bound, one-line, <=16KiB JSON record is parsed with an exact key/type schema,
duplicate-key rejection and fixed enum errors. The record contains runtime versions,
binding-load booleans, initialization booleans, and the actual safe controller
snapshot. No native error messages, QR images/URLs, identity, credentials or raw
private stdout/stderr are published. Shared diagnostic categories are fixed booleans.

## Actual isolated observation — 2026-10-29

The WSL run against `/tmp/qq-native-lab-20261027-pristine/opt/QQ` completed normally
in 0.29 seconds with exit code 0 under the explicit process-lifetime policy. All namespace/pivot/old-root/uid/NNP/capability gates were true.
The actual host was Electron 40.0.0, Node 24.11.1, modules 143, N-API 10.
The native wrapper loaded. Engine singleton, login singleton, safe method validation,
engine desktop initialization and login configuration returned successfully.
The original token-required attempt failed at `register_listener`: its add call
returned `undefined`, not a numeric removal ID. After explicit process-lifetime
policy authorization, the final actual snapshot is `phase='initialized'`,
`failure_code=null`, `registration_call_returned=true`, and
`listener_id_type='undefined'`. This proves initialize/configuration calls returned,
not that a native listener is operational. `listener_registration_verified=false`,
`listener_handle_available=false`, and `native_listener_removed=false` remain exact.
Controller logical disposal completed; `cleanup_required='process_exit'` is honored
by actual child exit, reported separately as `native_cleanup_by_process_exit=true`
and `process_retired=true`. No guessed removal occurred and no successful native
listener removal is claimed. No connect/QR/authentication/session operation was attempted.

`docs/qq-native-init-evidence.json` is the actual bounded runner report. All five
pristine hashes remained identical. `native_initialize_verified=true` is limited
to the explicit process-scope initialization milestone. `native_login_verified`,
`session_initialized`, `safe_to_login`, `independent_backend_ready`,
`full_sendrecv_verified`, and `production_cutover` remain false. Void registration
is evidence, not a reason to invent listener IDs, fake callbacks, change appid types,
assume a connect return contract, or expand execution permission.

## Validation

`python -m pytest tests/test_qq_native_init_control.py tests/test_qq_ephemeral_host_entry.py -q`
passes 54 tests covering shared delegation, fixed modes/sources, strict/safe output,
failure preservation and unchanged wrapper behavior. Including
`tests/test_qq_isolated_runtime_probe.py` gives 106 passed and 1 skipped. Python pyflakes and Node
syntax checks pass for the changed control sources. The isolated run is an actual
native attempt and deliberately reports failed status; unit success is not native
initialization success.
