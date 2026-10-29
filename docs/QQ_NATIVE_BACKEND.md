# Reusable native backend composition

`qq_transport/native_backend.js` exports CommonJS `createNativeBackend(options)`
and `NativeBackendError`. It composes the independently authored login, session,
and message controllers; it is not an inventory, loader, proxy, or config builder.
There are no NapCat imports, network operations, native-library loading, account
session guesses, credential defaults, routing defaults, or production changes.
Import and construction are dormant. Native calls occur only through explicitly
injected wrapper exports when the owner calls the corresponding operation.

## Explicit ownership contract

Pass `wrapper`, complete `engineConfig`, complete `loginConfig`, `globalAdapter`,
`listenerLifetime: 'process' | 'token'`, `sessionConfigFactory`,
`sessionOptionsFactory`, `dependsAdapter`, and `dispatcherAdapter`.
Optional `sendTimeoutMs` is delegated to the message controller's bounded timeout;
omitting it preserves that controller's existing policy, not a runtime config default.
No argument is sourced from process environment or another application.

1. `initialize()` delegates to `login.initialize()` in the preserved non-host generic
   adapter mode. Explicit optional `hostOptions: {sessionName, lifecycleMode}` opts
   into full native preparation: `host.prepare()` runs engine `get`, login `get`,
   startup `create`, session `getNTWrapperSession(sessionName)` (or explicit legacy
   session `create`) exactly once **before** constructing the login controller with
   `host.getLoginWrapper()`, subscribing its handler, then initializing engine/login.
   Cached facade getters are not repeated native factory invocations. Construction
   stays dormant; connect remains a separate action. No initialization failure is
   retried or switched to another ABI. Invalid login config can fail after native
   preparation; process cleanup remains required even if login construction fails.
2. `connect()` delegates only to `login.connect()`. Its return is an acknowledgement,
   not connection or authentication proof; the actual login callback is required.
3. `requestQRCode()` delegates only to `login.getQRCodePicture()` after the actual
   connected callback. Authentication requires the actual validated login callback.
4. The authenticated event reads `login.getIdentity()` internally. It does **not**
   initialize/start a session. After that event has been delivered, the owner calls
   `await initializeSession()` explicitly.
5. `sessionConfigFactory(identity)` is awaited first. It must return the complete
   secret session config from a trusted owner. Then `sessionOptionsFactory(identity)`
   is awaited and must return explicit `lifecycleMode` and `sessionName`, plus
   `legacyStartArgs` for legacy mode. The session module checks exact `selfUin` and
   `selfUid` equality against actual callback identity. Callback nonce/extra fields
   are not fused into config. The glue cannot supply a missing config. In host mode
   both options must exactly match prepared handle metadata; the session controller
   receives `preparedHandles: host.getPreparedSessionHandles()` and initializes the
   exact cached session without repeating startup/session factories.
6. `startSession()` explicitly delegates to session start. Init/start return values
   are not readiness evidence. The session controller requires its native post-start
   `onOpentelemetryInit({is_init: true})` callback; pre-start truth does not count.
7. `initializeMessaging()` is explicit and only succeeds after `session.isReady()`.
   It receives `session.getSessionForMessaging()`, the actual account identity, and
   a readiness function delegating to the session. No ready callback auto-starts it.
8. `send(request)` delegates once only after messaging is active and session is ready.
   Use the message module's existing supported text schema and explicit peer fields.
   The glue adds no IDs, duplicate suppression, retransmission, or routing policy.

Initialization attempts are single-use: concurrent/repeated session calls reject,
and native start/message initialization cannot be repeated. Session factories are
not retried on failure. Disposal is checked after both asynchronous factory awaits,
so late resolution cannot initialize native sessions. Drain asynchronous component
callbacks before taking a subsequent owner action (e.g. use the authenticated event,
not a native callback invocation immediately followed by initializeSession).

## Events and privacy

`subscribe(fn)` forwards immutable `{source: 'login'|'session'|'messages', event}`
envelopes, retaining each controller's own event schema. The login `qr` and
`authenticated` payloads are replaced with empty frozen objects in this generic
stream. There is no raw config or credential/identity forwarding in login status.
Message events retain the message controller's projected account/message fields;
they are application data and should not be treated as public telemetry.

Only explicit `subscribeQRCode(fn)` receives the immutable QR image/URL payload.
It is sensitive and must not be logged, persisted, replayed, or forwarded into
monitoring. Unsubscribe functions are repeat-safe. Subscriber exceptions and rejected
promises are swallowed without serializing error text, and cannot affect lifecycle.
No QR payload is cached by the backend. It has no logging interface.

`getSnapshot()` exposes safe component snapshots, attempt/failure state, disposal,
and only an `identity_available` boolean (never identity/config/credentials/QR data).
Before explicit host-mode initialize, `login` is `null` and `host.phase` is `new`;
pre-init connect/QR calls reject with fixed `INVALID_STATE`, never a null TypeError.
Non-host snapshots retain an eagerly constructed dormant login and `host: null`.
`initialize_failure_code` is fixed `INITIALIZE_FAILED` or null.
`isReady()` and `send_path_ready` mean **only that the limited session/message send
path is initialized and callback-ready with login still authenticated**, not that a
complete production backend exists or that all native QQ capabilities are covered.
`independent_backend_ready` remains false: this offline composition does not establish
full native capability parity. A synchronous login disconnect/failure revokes sends
before queued generic events arrive, and the session remains bound to its identity.
A native `result: 0` send response produces `state: 'accepted'`, never “delivered”.
Timeouts, exceptions and malformed acknowledgements remain `SEND_UNCERTAIN` with no
retry. Wrong readiness fails closed before message dispatch.

## Cleanup and verification boundary

`dispose()` marks disposed and clears/detaches subscriptions first, then attempts
messages, session, login, and optional host disposal in that order even if one fails.
Host disposal is logical only; it never guesses close/destroy APIs. Aggregate
failure exposes only fixed `DISPOSE_FAILED`, not native messages/causes. Further
operations reject; repeated disposal is locally safe. Component snapshots retain
`process_exit` requirements. Process-lifetime listeners returning void are never
removed using guessed handles, and session shutdown ABI is never invented. The
external owner remains responsible for the isolated process lifetime.

Run offline tests from the repository root:

```text
python -m pytest tests/test_qq_native_backend.py tests/test_qq_native_login.py tests/test_qq_native_session.py tests/test_qq_native_messages.py -q
node --check qq_transport/native_backend.js
```

The integration tests inject one fake wrapper with real engine/login/session/message
method names, drive queued callbacks, and exercise the **real three controller
modules end to end**. They do not load native QQ, execute login, send real messages,
access the network, prove real QQ compatibility, establish production readiness, or
claim capability parity. A real host ABI and complete owner-supplied runtime config
still require separately authorized validation; no such validation occurred here.
The old non-host generic adapter path is preserved, not certified as sufficient for
QQ 53644: its actual full native preparation path still requires version-specific
review. Consumer constructor-before-get ordering in the reviewed primary source
(base lines 704–728) is observation, not a Tencent ABI guarantee.
