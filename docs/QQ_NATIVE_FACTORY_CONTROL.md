# Offline native pre-initialize factory control

## Scope and source of the order

This is an explicitly authorized, one-run preparation experiment, not a login,
authenticated session, native callback proof, transport test, or production change.
The pinned primary NapCat consumer
([base.ts:704–728](https://github.com/NapNeko/NapCatQQ/blob/ba0a53dd372dbc4c4bfc83cf7132997459605210/packages/napcat-shell/base.ts#L704-L728),
commit `ba0a53dd372dbc4c4bfc83cf7132997459605210`) was verified by the
coordinating source-research agent to use this order:

1. `wrapper.NodeIQQNTWrapperEngine.get()`
2. `wrapper.NodeIKernelLoginService.get()`
3. `wrapper.NodeIQQNTStartupSessionWrapper.create()`
4. `wrapper.NodeIQQNTWrapperSession.getNTWrapperSession('nt_1')`
5. Engine desktop configuration and login configuration/listener setup.

The literal `nt_1` comes from that observed consumer. It is not a Tencent contract
or compatibility guarantee. No extra factory argument was guessed. Before calls,
all four named exports and static methods are checked as own data properties of
the expected function type, without invoking accessors.

The trusted probe obtains the actual engine/login instances exactly once, then
passes an explicit, frozen own-data facade to the existing `createLoginController`:
its two `get()` functions return those cached instances. The controller therefore
reuses the actual native results; it does not invoke the actual native getters a
second time. There is no Proxy, patch of QQ globals/native classes, or new
controller ABI. The returned startup/session objects are not inspected or used;
only their type descriptors and availability booleans escape.

## Shared boundaries

`tools/qq_native_factory_control.py` only selects the internal fixed mode
`initialize_factories` in `tools/qq_ephemeral_host_entry.py`. It stages the fixed
`tools/qq_native_factory_entry.js` and the same reusable native login/metadata
modules. Existing wrapper, initialize, connect, and vendor modes are unchanged.
There is no caller-selected script, QQ argument, metadata override, account input,
or fallback to an older initialization path on factory failure.

The real executable is the pristine software tree at
`/tmp/qq-native-lab-20261027-pristine/opt/QQ`. It runs as `/opt/QQ/qq`, with no QQ
flags, `ELECTRON_RUN_AS_NODE`, fuse edits, or security relaxation. The existing
runner supplies fresh network/mount/PID/IPC namespaces, pivot-root with detached
oldroot, private X/DBus/fonts/data, UID 65534, no-new-privileges and zero effective
capabilities. The existing `verify_offline_network` requires only DOWN loopback
and no forwarding routes. Fixed read-only entry/manifest overlays are private to
the jail; the pristine package metadata is staged separately unchanged. Public
version metadata remains appid `537391664` and QUA
`V1_LNX_NQ_3.2.34_53644_GW_B`, exactly as the prior initialize control.

Before and after every factory call and before/after controller initialization
and disposal, a synchronous nonce-bound progress record is written. Only fixed
stage names, type descriptors and booleans appear. A blocked call would leave its
before-call checkpoint; the shared QQ 30-second / outer 45-second bounds remain.
Final parsing reuses the exact existing initialize envelope validator and adds
strict factory/stage/binding/initialization coherence. Private stdout/stderr are
represented only by existing fixed diagnostic-category booleans, never raw text.

No `session.init`, session start, `startNT`, login connect, QR/auth/password,
account metadata, or native teardown is called. Controller process-lifetime
disposal and fixed Electron `app.exit` retire the child without guessed native
cleanup or timers.

## Actual result — 2026-10-29

The single authorized execution completed in **0.33 seconds**, with normal
application exit **0** and a strict final `ok=true` record. The nonce-bound
checkpoint reached `after_dispose`. Actual engine, login, startup and session
factory results all had type **object**:

- `startup_factory_created=true`: startup factory returned an object, not readiness.
- `session_factory_available=true`: named session factory returned an object, not
  an initialized or usable authenticated session.
- `native_initialize_verified=true`: the reused controller completed engine
  desktop initialization, login configuration and listener registration call.
- Listener registration returned `undefined`; no native listener token/removal or
  callback registration proof is inferred. `native_listener_registered=false`,
  `native_listener_disposed=false`; cleanup is process retirement.
- `connect_result_type=not_attempted`, all seven tracked native callback counts
  zero, authenticated/QR/session initialized/send-receive/backend-ready/cutover
  flags false. ABI compatibility remains unverified.
- All five host software SHA-256 hashes were identical before/after (executable,
  package manifest, native wrapper, application ASAR and outer launcher).
- Every required isolation and explicit offline-network boolean passed.

The complete safe observation is in `docs/evidence/qq-native-factory-evidence.json`.
The actual executed JS SHA-256 is
`b0005d017732ecece8f7a19894991094035176d0bb466326eb6d9792bdd37261`.
Staged controller/metadata/pristine-package source hashes are retained in the
same evidence so the executed source, rather than later worktree contents, is
identifiable. The experiment does not establish that these factories are a
required fix, nor that any later native callback/connect/session behavior works.
No second native run was made or authorized by this task.

## Validation

`python -m pytest tests/test_qq_native_factory_control.py tests/test_qq_native_init_control.py -q`
passed **46 tests**. Tests never execute QQ. They validate the actual redacted
record, five unchanged hashes, offline/isolation flags, fixed source order,
cached facade, forbidden calls, strict non-readiness, bounded/nonce-bound progress,
and rejection of forged stages, descriptors, identity fields or auth/connect
claims. Python compilation, JS syntax checking and focused pyflakes passed.
Broader suite results are owned and reported by the coordinating agent.
