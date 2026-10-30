# Explicit native host factory preparation

`qq_transport/native_host.js` is a concrete reusable **factory prerequisite**, not
an inventory, native loader, account/config builder, login initializer or session
starter. It imports no QQ code and constructs no native class. The trusted owner
supplies the matching wrapper exports and explicitly decides when to call native
factories. Module tests inject fakes only; this implementation work runs no QQ
native code, network operation or deployment.

## Contract

```js
const {createNativeHost, prepareNativeHost} = require('./qq_transport/native_host');
const host = createNativeHost({wrapper, sessionName, lifecycleMode: 'startup'});
// Dormant: no factory calls occurred above.
host.prepare();
const loginWrapper = host.getLoginWrapper();
// Pass loginWrapper to the existing login controller; it returns cached objects.
const preparedHandles = host.getPreparedSessionHandles();
// Only the trusted session owner may read these handles, never JSON-log them.
```

`prepareNativeHost(options)` is a convenience **explicit function invocation**:
it creates a host, invokes `prepare()` and returns the prepared host. It is not a
dormant constructor alias. `prepare()` returns a safe snapshot, not raw handles.
Repeated preparation is rejected, including after success, failure or disposal.

Exactly three own options are accepted: `wrapper`, `sessionName`, `lifecycleMode`.
No account identity, login/engine/session config or other parameter bag is retained.
`sessionName` has no default: it must be a nonblank string of at most 256 UTF-16
code units without ASCII control characters. The supplied string is preserved,
not trimmed or replaced. `lifecycleMode` must be explicitly `startup` or `legacy`.
Wrapper/config references are trusted-owner input, not a security boundary.

## Factory order and failure

Before any native factory invocation, `prepare()` validates all selected wrapper
exports as own data properties and all selected static methods as own callable
data properties. Inherited factories and accessor properties are not invoked.
Factories retain their original `this` receiver. No constructor, Proxy facade,
global monkey patch, alternate factory probing or catch-and-fallback is used.

Startup order, exactly once:

1. `NodeIQQNTWrapperEngine.get()`
2. `NodeIKernelLoginService.get()`
3. `NodeIQQNTStartupSessionWrapper.create()`
4. `NodeIQQNTWrapperSession.getNTWrapperSession(sessionName)`

Explicit legacy order, exactly once:

1. `NodeIQQNTWrapperEngine.get()`
2. `NodeIKernelLoginService.get()`
3. `NodeIQQNTWrapperSession.create()`

Legacy does not require/touch the startup export. It is never selected as a
fallback after a startup failure. Returned handles must be nonnull objects or
functions; the host does not inspect their native contents. Preparation does not
call engine/login `init`, config, connect, listener registration, session `init`,
startup `start`, `startNT`, message services or any cleanup method.

Any wrapper validation failure is terminal `INVALID_WRAPPER` before native work.
Any factory throw or invalid return is terminal `PREPARE_FAILED`; no retries are
allowed even if the native side-effect status is uncertain. Errors have fixed
messages/codes and never retain the original error, cause, config, argument or
native diagnostic. An invalid call state gives `INVALID_STATE`; option validation
uses `INVALID_CONFIG`.

## Ownership surfaces

- `getLoginWrapper()` requires `prepared` and returns one cached frozen plain
  facade. Its own `NodeIQQNTWrapperEngine.get` and
  `NodeIKernelLoginService.get` return the already acquired real objects, never
  invoke a second native `get`, and reject access after host disposal.
- `getPreparedSessionHandles()` requires `prepared` and returns a frozen own-field
  record `{startupSession, session, lifecycleMode, sessionName}`. `startupSession`
  is `null` in legacy mode. This is an explicit raw native capability for trusted
  lifecycle owners only, **not telemetry**. Do not log/serialize it. Freezing the
  record does not freeze native objects or revoke handles previously read.
- `getSnapshot()` returns a frozen record containing only `phase`
  (`new|prepared|failed|disposed`), `engine_available`, `login_available`,
  `startup_available`, `session_available`, fixed/null `failure_code`,
  `process_cleanup_required` and `cleanup_required` (`none|process_exit`). No
  session name, identity, account, config, arguments or actual handles appear.
- `dispose()` is idempotent logical release. It drops held references and disables
  facade getters. It does not call remove/destroy/close or guess cleanup ABI.
  `process_cleanup_required` becomes true immediately **before the first native
  get attempt**, including a throwing first call, and remains true after dispose.
  Only owning-process exit satisfies cleanup; the host does not exit the process.
  Disposal also wins if a factory synchronously reenters owner code during
  `prepare()`: a guard immediately before and after each factory prevents further
  calls and prevents storing the just-returned handle. Earlier cached handles are
  cleared, the phase stays `disposed`, and `prepare()` throws fixed `INVALID_STATE`
  whether that factory returns or throws. No subsequent factory, retry or native
  cleanup is attempted; `process_cleanup_required` remains true.

Session integration must explicitly consume prepared instances, with ownership
and lifecycle-mode/name validation, instead of asking factories again. The
preparation module deliberately does not wrap the existing session controller:
without prepared-handle injection support such a helper would duplicate native
factories. Backend/session integration is separate owner work; this module alone
does not claim integration, login, readiness or send/receive support.

## Evidence and limits

The pinned primary **consumer** is
`NapNeko/NapCatQQ@ba0a53dd372dbc4c4bfc83cf7132997459605210`,
`packages/napcat-shell/base.ts:704–728`, with startup session factories in the
same file at `677–688`. The observed consumer obtains engine and login, creates
startup, obtains the explicitly named session, then initializes engine/login.
This is a pinned consumer ordering observation, **not a Tencent ABI guarantee**.
The consumer's literal session name is not this module's default.

Existing local `docs/evidence/qq-native-factory-evidence.json` records an actual QQ
3.2.34-53644 host experiment: the factory prerequisite order followed by
engine/login initialization succeeded. That artifact already exists; this task
neither reruns nor rewrites it. It does not prove account authentication, session
readiness, messaging or general version compatibility. The previous backend
omitted pre-login startup/session creation; explicit owner integration must fix
that omission without obtaining duplicate factories. All new module tests are
fake-only and make no additional actual-native claim.

## Offline validation

```text
node --check qq_transport/native_host.js
python -m pytest tests/test_qq_native_host.py -q
```

Coverage includes dormancy, exact factory order/arguments, explicit legacy mode,
static validation before any native work, once-only preparation, identity-cached
plain login facade, explicit trusted raw handles, invalid names, no account
fields, fixed errors/safe snapshots, failure without fallback/retry, and logical
disposal without native cleanup, and synchronous disposal at every startup/legacy
factory boundary (both return and throw outcomes) without handle resurrection.
