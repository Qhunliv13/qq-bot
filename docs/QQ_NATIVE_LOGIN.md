# Injected native login lifecycle

## Status and scope

`qq_transport/native_login.js` is a reusable CommonJS lifecycle module, not a
native-library loader or a complete QQ client. This change has **only offline
injected-wrapper test evidence**. Native Tencent-host loading is a separately
tracked milestone; this module's tests do not prove that actual engine
initialization, connection, QR retrieval or authentication currently works.
Do not describe these tests as a real login. No live account or production action
is part of this change.

The module has no dependency on another QQ runtime implementation and never
imports native exports. It performs no import-time native calls, file writes,
password login, session initialization, polling, retry, network shutdown, process
shutdown, guessed destroy/offline calls, or implicit QR requests. Connection is a
separate explicit operation that deployment policy must authorize externally.
There is deliberately no combined `start()`.

## Contract provenance

The primary pinned contract is commit
`ba0a53dd372dbc4c4bfc83cf7132997459605210` of the upstream source:

- Factory/config/listener order: [base.ts](https://github.com/NapNeko/NapCatQQ/blob/ba0a53dd372dbc4c4bfc83cf7132997459605210/packages/napcat-shell/base.ts), especially lines 84–128, 235 and 704–721.
- Login methods/callback contract: [NodeIKernelLoginService.ts](https://github.com/NapNeko/NapCatQQ/blob/ba0a53dd372dbc4c4bfc83cf7132997459605210/packages/napcat-core/services/NodeIKernelLoginService.ts).

These are source references, not runtime dependencies or evidence that the same
contract works in the installed host. The owner must supply configuration derived
from **the actual same-version metadata**; this module cannot attest its origin.
No deployment metadata or account defaults ship here.

The complete explicit listener surface is taken from the same pinned
`packages/napcat-core/listeners/NodeIKernelLoginListener.ts`: eighteen named methods,
including `onQRCodeSessionUserScaned` and `OnConfirmUnusualDeviceFailed` with their
original spellings. The seven supported lifecycle callbacks retain the projected
state behavior below. Eleven additional notification methods deliberately do nothing
and do not inspect arguments, grant authentication, retain account records, initiate
fallbacks or expose raw payloads. They are explicit methods, not a catch-all Proxy;
this completes the observed callback surface without changing the seven-counter
telemetry schema. Their presence alone is not proof that native callbacks work.

Pinned consumer ordering also creates the startup-session/session factory handles
**before** engine/login configuration; actual session initialization/start still
occurs only after successful login. `native_login.js` does not silently create those
additional handles. The host owner must establish and verify any such prerequisite
outside the controller, and must not assume later post-login factory creation is
interchangeable. Separate fixed native-factory controls document this remaining
host-integration question; no guessed fallback or session start belongs here.

## Public API

```javascript
const { createLoginController } = require('./qq_transport/native_login');
const controller = createLoginController({
  wrapper,       // exports supplied by the authorized Tencent host owner
  engineConfig,  // explicit validated same-version object, described below
  loginConfig,   // explicit validated same-version object, described below
  globalAdapter, // host-owned object/function required by engine initialization
  clock,         // optional () => finite epoch milliseconds; defaults Date.now
  listenerLifetime // optional 'token' (default) or explicitly authorized 'process'
});
const unsubscribe = controller.subscribe(event => consumeWithoutLogging(event));
controller.initialize(); // singleton gets, engine init, login init, listener add
// Deployment owner separately authorizes controller.connect(); never implicit.
```

`createLoginController()` validates/copies configuration and validates the
wrapper's own data-property exports `NodeIQQNTWrapperEngine` and
`NodeIKernelLoginService`, each with an own data-property static `.get` function.
Accessor/inherited exports and accessor `.get` are rejected without execution.
It does not invoke either function. `initialize()` obtains both singletons,
validates descriptor-backed methods (instance or at most three prototypes), calls
`engine.initWithDeskTopConfig(engineConfig, globalAdapter)`, then
`login.initConfig(loginConfig)`, then `addKernelLoginListener(listener)`. It accepts
only an actual nonnegative safe-integer listener ID by default (`listenerLifetime:
 'token'`). One isolated parent-owned actual-host probe measured a void
`undefined` registration return despite the pinned numeric annotation. To support
that observed contract **without inventing a handle**, an owner may explicitly
select `listenerLifetime: 'process'`. Only then is `undefined` also accepted; all
strings/objects/other values remain rejected. A void return proves only that the
call returned, not registration delivery: `listener_registration_verified` stays
false until an actual callback. Process ownership must cover ending the isolated
host process; the controller never exits it. This is an explicit ownership
policy, not a version/account special case or automatic fallback. Only one
initialization attempt is allowed, including when the native outcome is uncertain.

`connect(): boolean` is legal only after initialization and only once. A true
return is a **request acknowledgement**, not connection success or identity.
Only actual `onLoginConnected()` opens the QR gate. `getQRCodePicture(): boolean`
is one explicit request after that callback; true is only an acknowledgement.
There is no retry after false, exception, disconnect, terminal event or uncertain
outcome. The owner must decide whether/how to recover outside this controller;
creating a replacement must not be used to blindly retry a still-active native
singleton.

`subscribe(callback)` returns an idempotent unsubscribe function. Events are
asynchronously delivered immutable `{type, phase, at, payload}` projections.
Types are `initialized`, `connecting`, `connected`, `qr_pending`, `qr`,
`authenticated`, `failed`, `disconnected`, and `listener_error`. Synchronous throws
and rejected promises in one subscriber are isolated; other subscribers receive
only `{code: 'LISTENER_FAILED'}` without original error text. Error reporting does
not recursively report failures of error-event consumers. Unsubscribe/dispose
suppresses queued deliveries. Callbacks may be synchronous/reentrant inside
native methods; the controller sets request state before entering native code.

The QR event alone projects `{pngBase64QrcodeData, qrcodeUrl}`. Validation requires
canonical base64 (plain or exact `data:image/png;base64,` prefix) containing a
bounded PNG container (maximum 1 MiB decoded,
IHDR, bounded nonzero dimensions, bounded complete chunks, IDAT and final IEND),
and an HTTPS URL (maximum 4096 characters, no credentials/control/whitespace).
It does not inflate images or claim full PNG decoding/CRC integrity validation.
The consumer must handle the image as untrusted sensitive input. It must not put
QR events into JSON diagnostics, analytics, logs, error messages or durable
storage. The controller itself logs nothing and does not retain QR image/URL.

Authentication is recognized only from `onQRCodeLoginSucceed` after the explicit
QR request and connected callback. It projects only nonempty bounded string
`uin` and `uid`; there are no generated/fallback account IDs and no forwarding of
arbitrary raw callback fields. Native failure/disconnect arguments are discarded.

`getSnapshot()` returns frozen `{phase, disposed, qr_available, authenticated,
failure_code, initialization_step, failed_stage, registration_call_returned,
listener_id_type, listener_lifetime, listener_handle_available,
listener_registration_verified, native_listener_removed, cleanup_required,
connect_call_returned, connect_result_type, native_callback_counts}`.
`connect_call_returned` reports only whether the call returned;
`connect_result_type` is `not_attempted`, `threw`, `boolean`, `undefined`, or `other`.
Connect still strictly requires boolean acknowledgement; these fields do not
accept void results or permit retries. `native_callback_counts` is a frozen copy
of exactly the seven registered callback names, each saturated at 100. Counts
increment before lifecycle gating and contain no callback arguments; they can
reveal delivery after failure without authenticating or changing lifecycle state.
`listener_lifetime` exposes the explicit ownership policy. The milestone fields are null
or fixed safe milestone labels: `factory_engine`, `factory_login`,
`validate_methods`, `engine_config`, `login_config`, `register_listener`,
`initialized`. A label is set before the corresponding operation; initialization
failure copies it to `failed_stage`. `registration_call_returned` is false until
the registration call actually returns; `listener_id_type` is the fixed enum
`not_attempted`, `threw`, `number`, `string`, `undefined`, or `other`. These expose
neither the returned ID value nor native error/configuration. Nonnumeric IDs
remain rejected except explicitly process-owned void registration, without
coercion or guessed removal.
The snapshot contains no QR, URL, uin or uid. `getIdentity()` separately and
explicitly returns frozen `{uin, uid}` after a validated authentication callback,
or `null`. Account identity remains sensitive even when explicitly read.

Honest phases: `new`, `initialized`, `connecting`, `connected`, `qr_pending`,
`authenticated`, `failed`, `disconnected`. `qr_pending` means requested, **not**
that an image arrived; use `qr_available` to distinguish. Failure clears identity
and QR availability. Late callbacks after failure/disconnection/disposal are
ignored. Unexpected QR/auth callbacks before the connected/request gate fail
closed rather than fabricating progress.

`dispose()` disables callbacks, clears subscriptions/identity and calls only
`removeKernelLoginListener(id)` using the actual returned ID, at most once. It is
idempotent even if removal throws. It does not disconnect the underlying native
transport or stop QQ. A listener-registration exception/invalid returned ID
cannot safely be cleaned up with a guessed identifier; host ownership must handle
that uncertain state. `disconnected` after dispose describes this controller,
not proof the native socket stopped. It returns the safe snapshot, including
`cleanup_required`: `none`, `remove_listener` (numeric registration still owned),
or `process_exit` (void/uncertain registration or failed removal).
`listener_handle_available` describes an actually held numeric token;
`native_listener_removed` becomes true only when exact-ID removal returns.
Process-owned void registration keeps `native_listener_removed: false` after
logical disposal and requires the external supervisor to retire the process.
No controller code performs that retirement or asserts it happened.

## Explicit configuration validation

Unknown keys, missing keys and accessor fields are rejected. Copies/nested config
objects are frozen before entering native calls. No silent string/number coercion
is performed.

- Engine exact keys: `base_path_prefix`, `platform_type`, `app_type`, `app_version`,
  `os_version`, `use_xlog`, `qua`, `global_path_config`, `thumb_config`.
- This bounded observed contract supports only explicit `platform_type: 5`
  (Linux) and `app_type: 4`; neither is defaulted or inferred.
- `base_path_prefix` is the explicitly supplied string (the pinned source allows
  literal `''`); nonempty prefixes must be absolute POSIX container paths.
  `app_version`, `os_version`, `qua` are nonempty bounded strings;
  `use_xlog` is boolean.
- `global_path_config` has exactly `desktopGlobalPath`, an absolute POSIX path.
- `thumb_config` has exactly finite positive numeric `maxSide`, `minSide`,
  `longLimit`, `density`; `minSide <= maxSide`.
- Login exact keys: `machineId`, `appid`, `platVer`, `commonPath`, `clientVer`,
  `hostName`, `externalVersion`.
- `machineId` must be literal `''`. `appid` must be an explicit positive safe
  integer **or** a 1–32 digit string; its original type/value is retained because
  source metadata and typed declarations differ. No automatic compatibility
  fallback is attempted.
- `platVer`, `clientVer`, `hostName` are nonempty bounded strings;
  `externalVersion` is boolean; `commonPath` is an absolute POSIX container path.
- Paths reject backslashes, `..` components and control characters. The owner
  must provision actual container directories/permissions; no filesystem
  existence checks or creation occur here. `globalAdapter` is required and
  host-owned; this module does not guess its methods.

Failures throw `NativeLoginError` with fixed `code`/message only, never native
messages, stacks as causes, credentials or raw payloads. Codes are
`INVALID_CONFIG`, `INVALID_WRAPPER`, `INVALID_STATE`, `INITIALIZE_FAILED`,
`CONNECT_FAILED`, `CONNECT_REJECTED`, `QR_REQUEST_FAILED`, `QR_REQUEST_REJECTED`,
`INVALID_EVENT`, `LOGIN_FAILED`, `QR_SESSION_FAILED`, `REMOVE_LISTENER_FAILED`,
`LISTENER_FAILED`. False acknowledgements return false and record terminal
failure; thrown/malformed native outcomes throw sanitized fixed errors.

## Offline verification

```powershell
python -m pytest tests/test_qq_native_login.py -q
node --check qq_transport/native_login.js
python -m pyflakes tests/test_qq_native_login.py
```

The pytest suite stores temporary JavaScript test files and launches Node through
`subprocess.run` (Windows-compatible argument list, no shell). Synthetic fixtures
exercise explicit method ordering, no calls before initialization, reentrant
callbacks, booleans not fabricating QR/identity, immutable projections, safe
snapshots, invalid descriptors/config/events, no repeat after uncertain outcome,
listener isolation and exact-ID/idempotent removal. It imports no native QQ code
and performs no network, QR scan, account login or production action.
