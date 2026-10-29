# Native session lifecycle (consumer contract, not runtime certification)

`qq_transport/native_session.js` is an independent CommonJS module. Requiring it
or constructing a controller makes **no native calls**, creates no files, starts
no timers and loads no native library. It does not log in, build session config,
request credentials, access message services or run a production connection.

## Evidence and deliberate limits

Source reviewed on 2026-10-29: NapNeko/NapCatQQ commit
`ba0a53dd372dbc4c4bfc83cf7132997459605210`:

- [base.ts, factories lines 677–688](https://github.com/NapNeko/NapCatQQ/blob/ba0a53dd372dbc4c4bfc83cf7132997459605210/packages/napcat-shell/base.ts#L677-L688): startup `create()` precedes static session `getNTWrapperSession(name)`. The source's literal session name is **not** a module default.
- [base.ts, init/start/readiness lines 546–579](https://github.com/NapNeko/NapCatQQ/blob/ba0a53dd372dbc4c4bfc83cf7132997459605210/packages/napcat-shell/base.ts#L546-L579): `init(config, dependsAdapter, dispatcherAdapter, listener)`, startup `start()` or legacy `startNT`, and consumption of `onOpentelemetryInit(info.is_init)`.
- [base.ts, post-login config construction lines 713–735](https://github.com/NapNeko/NapCatQQ/blob/ba0a53dd372dbc4c4bfc83cf7132997459605210/packages/napcat-shell/base.ts#L713-L735): identity and machine GUID originate outside session initialization. The upstream `genSessionConfig` call is not evidence that this module knows a complete minimal schema or has authenticated account data.

These are observations of an upstream consumer, **not Tencent ABI guarantees**,
proof of compatibility with another installed runtime, or proof of successful
account login. Upstream tests truthiness of `info.is_init`; this module deliberately
accepts only an own data property whose value is the exact boolean `true`.
Upstream catch-based factory and start-overload retries are deliberately **not**
reproduced: an exception can mean partial native initialization.

## Required owner input

```javascript
const {createSessionController} = require('./qq_transport/native_session');
// authenticatedIdentity must originate from completed login, for example
// loginController.getIdentity(); reject null outside this module.
const controller = createSessionController({
  wrapper,                         // already loaded matching native exports
  sessionConfig,                   // caller-supplied complete JSON record
  dependsAdapter, dispatcherAdapter,
  accountIdentity: authenticatedIdentity, // {uin, uid}; no authenticated flag
  sessionName,                     // explicit nonblank string, never defaulted
  lifecycleMode                   // explicit 'startup' or 'legacy'
  // legacyStartArgs: [] OR [0]     // REQUIRED in legacy mode; select known ABI
});
// Creation is dormant. Only the owning process explicitly authorizes these:
controller.initialize();           // factories + init only; never starts
controller.start();                // exactly one selected start call
// Later, after actual post-start onOpentelemetryInit({is_init:true}):
if (controller.isReady()) {
  const nativeSession = controller.getSessionForMessaging();
  // Inject nativeSession into the separately owned message adapter here.
}
```

Do not substitute an identity fabricated from session config. The owner must
establish it from actual external login, e.g. `native_login.getIdentity()` after
its authenticated callback, and stop if unavailable. An injected object alone
is **not authorization**: this module is not a security boundary, cannot prove
provenance, and does not prevent the caller using its own raw wrapper directly.
Engine/Login initialization alone is not login and does not make messaging ready.

`sessionConfig` must be a plain JSON record. Validation requires own fields:

- `selfUin` / `selfUid`: exact string equality to external `uin` / `uid` (nonblank,
  at most 256 characters, no control characters).
- `clientVer`: explicit nonblank string, at most 4096 characters, no controls.
- `desktopPathConfig.account_path`: explicit absolute POSIX, drive-qualified
  Windows or UNC path; no environment lookup, path resolution or directory creation.
- `machineId`: string; the observed empty string is allowed, never generated.
- `deviceInfo`, **if supplied**, must be a JSON object. Its full required schema
  and whether it is mandatory for the selected runtime remain the owner's duty.

Unknown JSON keys and nested data are preserved, including any required native
credentials: they are not projected into events, snapshots or errors. There is
no guessed config builder, generic defaults, hardcoded account/GUID/version,
or automatic secret acquisition. The caller must supply all additional
same-version fields; passing these checks does not establish native completeness.

Config is deep-detached before any native access. JSON values only: no accessors
(even ignored fields), proxies, symbols, functions, undefined, nonfinite numbers,
custom object prototypes, cycles or sparse arrays. Limits are depth 32, 20,000
visited values, 20,000 keys per container and 1 MiB combined JS string/key code
units. Object property definition preserves unusual JSON keys without prototype
assignment. Adapters are intentionally passed by identity, not JSON-cloned.

## Lifecycle and observability

- Phases: `new → initialized → starting → ready`; errors are terminal `failed`;
  logical `dispose()` leads to terminal `disposed` and is repeatable.
- Startup mode: `NodeIQQNTStartupSessionWrapper.create()` then
  `NodeIQQNTWrapperSession.getNTWrapperSession(sessionName)`; start calls the
  **startup instance** with zero arguments. Missing startup APIs fail; legacy
  must be explicitly chosen by the owner from known runtime metadata.
- Legacy mode: `NodeIQQNTWrapperSession.create()` only, then `session.startNT`
  with exactly the explicit `legacyStartArgs`. No overload probing or retries.
- Init/start native methods must return `undefined` under this explicit void ABI.
  Neither void return proves readiness. A boolean/other return is not promoted
  to success. A callback delivered synchronously inside start is committed only
  after start returns normally; a later exception cannot leave ready state.
- A positive callback before start does not authorize messaging; another actual
  callback after the start attempt is required. Negative/malformed readiness
  callbacks after init are terminal failures. Late callbacks after failure or
  disposal are ignored. No automatic recovery, polling or retry timer exists.
- `getSessionForMessaging()` returns the exact native session only while ready.
  It never calls `getMsgService()`; service ownership stays in the message module.
- `getSnapshot()` returns a frozen projection `{phase, is_ready, failure_code,
  native_cleanup_required}`. Failure codes are fixed enums, without raw exception
  messages/causes. No config, identity, paths or secrets are returned.
- `subscribe(callback)` delivers frozen `{code, phase, payload}` events in a
  microtask and returns an unsubscribe function. Codes are `INITIALIZED`,
  `STARTING`, `READY` or fixed failure codes. Only `READY` includes
  `{is_init:true}`; other payloads are empty. Arbitrary raw callback fields are
  never read. Subscriber errors/rejections are swallowed, not logged/retried.
- `dispose()` is **logical only**, never invokes guessed close/destroy/listener
  removal APIs. Once a native factory has been attempted,
  `native_cleanup_required` is `process_exit`, even after exceptions/disposal.
  The owner must isolate native work in a disposable process and arrange exit.
  Before native work it is `none`. This module never exits the process itself.

No account login or actual session initialization has been established by these
offline tests. Runtime compatibility, valid complete config, native callback
availability and teardown behavior remain unverified until an authorized,
isolated owner performs the appropriate runtime validation.

## Offline verification

`python -m pytest tests/test_qq_native_session.py -q` uses only injected fake
wrappers. It checks dormant construction, startup/legacy ordering, exact start
arguments, separate start, callback-only readiness, identity mismatch before
factories, detached bounded JSON, safe errors/events, no retries, no native close
and no hidden message-service access. No production network or native library
is used by this suite.
