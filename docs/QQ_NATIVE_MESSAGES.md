# Native QQ message adapter (offline contract only)

## Scope and evidence boundary

`qq_transport/native_messages.js` is independently authored, first-class message
conversion and dispatch code. It does not import NapCat, a public/network API,
login code, native libraries, or a persistence layer. It is the sole message
conversion implementation for this adapter. The caller supplies an **already
created explicit session** and a session lifecycle `isReady()` function. Login
initialization, a QR callback, engine creation, or this adapter's `active` phase
must never be represented as logged-in/session-ready evidence.

As of 2026-10-29, these tests use fake native objects only. No logged-in native
session, native message receive/send, delivery, or listener-removal ABI has been
verified. A real wrapper/engine/login initialization elsewhere does not change
this boundary. Native integration remains unverified.

Reviewed ABI declaration evidence is pinned to
`NapNeko/NapCatQQ@ba0a53dd372dbc4c4bfc83cf7132997459605210`:

- [Session getMsgService declaration](https://github.com/NapNeko/NapCatQQ/blob/ba0a53dd372dbc4c4bfc83cf7132997459605210/packages/napcat-core/wrapper.ts): session provides the message service.
- [Message service declarations](https://github.com/NapNeko/NapCatQQ/blob/ba0a53dd372dbc4c4bfc83cf7132997459605210/packages/napcat-core/services/NodeIKernelMsgService.ts#L7-L17): generate takes `(chatType, time: string)` and returns string; send takes `(msgId, peer, elements, Map)` and returns `Promise<GeneralCallResult>`.
- [Listener declarations](https://github.com/NapNeko/NapCatQQ/blob/ba0a53dd372dbc4c4bfc83cf7132997459605210/packages/napcat-core/listeners/NodeIKernelMsgListener.ts#L239-L280): `onMsgInfoListUpdate` and `onRecvMsg` each receive a message array.
- [Peer / raw message declarations](https://github.com/NapNeko/NapCatQQ/blob/ba0a53dd372dbc4c4bfc83cf7132997459605210/packages/napcat-core/types/msg.ts): explicit peer UID, optional guild ID, raw IDs/time/sequence/random as strings, private chat 1 and group chat 2.
- [Text declaration](https://github.com/NapNeko/NapCatQQ/blob/ba0a53dd372dbc4c4bfc83cf7132997459605210/packages/napcat-core/types/element.ts#L31-L37): all five text fields are supplied. The declared send-element base contains `elementId`, but this adapter does not guess an ID or claim the runtime requires/accepts an omitted one. Only an explicitly supplied ID is forwarded.
- [GeneralCallResult](https://github.com/NapNeko/NapCatQQ/blob/ba0a53dd372dbc4c4bfc83cf7132997459605210/packages/napcat-core/services/common.ts#L1-L8): status 0 / -1 and textual `errMsg`; acknowledgement does not establish delivery.

These are declaration/consumer evidence, not proof of the currently installed
native ABI. No source implementation is copied into this module.

## Public API

CommonJS exports: `createMessageController`, `NativeMessageError`, `LIMITS`.

```javascript
const { createMessageController } = require('./qq_transport/native_messages');
const messages = createMessageController({
  session: explicitSession,
  accountIdentity: { uin: observedUin, uid: observedUid },
  isReady: () => sessionLifecycle.isReady(),
  listenerLifetime: 'process', // explicit host-process ownership; see below
});
const unsubscribe = messages.subscribe(event => consume(event));
messages.initialize(); // only after sessionLifecycle.isReady() === true
```

Creation, subscription and `getSnapshot()` are dormant: no service getter,
listener registration, ID allocation or dispatch occurs. Identity strings are
copied/frozen, never inferred. `initialize()` is single-attempt after native work
starts; a preflight `NOT_READY` leaves it dormant. It obtains
`session.getMsgService()` using own/prototype **data descriptors**, validates all
four native methods before registering, and rejects accessor-backed methods
without invoking them. Prototype traversal is bounded to four objects.

- `initialize()` returns a frozen local diagnostic snapshot.
- `subscribe(fn)` returns local unsubscribe; subscribers receive queued events.
- `send(request)` returns a promise; see acknowledgement semantics below.
- `dispose()` is idempotent, disables callbacks first, and follows the explicit listener policy.
- `getSnapshot()` returns `{phase, listener_lifetime, cleanup_required, dropped_batches}`. Phase is adapter state only; it is **not** session readiness or login status. Snapshot contains no account identity or message text.

### Listener lifetime and the unresolved token contradiction

The pinned service declares `addKernelMsgListener(...) -> number`, but declares
`removeKernelMsgListener(listenerId: string)`. No real runtime evidence resolves
this contradiction. The adapter never guesses `String(handle)` or any opposite
conversion:

- Default `listenerLifetime: 'token'`: registration must return an actual safe
  integer number or canonical nonnegative decimal string (max 256 characters).
  Disposal passes the **exact original value and type** to remove. This is an
  explicit caller contract, not a claim that the unresolved native ABI works.
- Optional `removalIdEncoder(handle)` is the sole way to select another removal
  representation. Only provide one after independent runtime verification; its
  result is validated. No conversion is enabled by default.
- `listenerLifetime: 'process'`: accepts a void registration result. Disposal is
  **local only**, does not call native remove, and reports
  `cleanup_required: 'process_exit'`. Native listener reclamation belongs to the
  owning process, not a fake unsubscribe promise.
- A thrown/invalid registration may have registered natively already: no retry or
  invented token is attempted; process cleanup remains required. A removal error
  is sanitized to `REMOVE_LISTENER_FAILED` and requires process cleanup.

## Incoming events and privacy

The listener has exactly `onRecvMsg(rawList)` and
`onMsgInfoListUpdate(rawList)`. Each valid callback produces exactly **one event**
with one immutable array, preserving native order:

```javascript
{ type: 'recv', payload: [projectedMessage, ...] }
{ type: 'update', payload: [projectedMessage, ...] }
```

Each projected message includes:

- `account: {uin, uid}`, exact `msgId` string, `chatType`, exact `peerUid` string.
- Exact original optional `senderUid`, `senderUin`, `peerUin`, `msgTime`, `msgSeq`, `msgRandom` strings when present; numeric conversion is never performed.
- `elements`: text (`elementType: 1`) with the five bounded text fields; optional explicitly present `elementId`. Unknown element types expose **only** `elementType` and optional `elementId`. Their content, files, paths and metadata are deliberately discarded. This does not claim image/attachment/rich-message support or full raw-payload preservation.
- Frozen `key: [account.uin, account.uid, chatType, peerUid, msgId]`: an identity tuple, not a universal/global uniqueness guarantee. Repeated native callbacks are not deduplicated.

Own data descriptors are used for projected fields and array indexes. Accessors
are rejected without executing them, unknown properties are not enumerated or
cloned, and all retained nested containers are frozen. This guards normal data
objects, **not hostile Proxy traps**: the caller owns/trusts native object
provenance. No raw payloads, native error strings, account identity or unknown
metadata are logged by this module. Consumers must apply their own privacy and
retention policy to message events.

A batch exceeding 100 messages, malformed projected field, indexed getter,
unsupported field type, or oversized text fails the **entire** callback closed;
no silently truncated message is published. A sanitized
`{type:'invalid_event', payload:{code:'INVALID_EVENT'}}` is queued instead.
Text content is bounded to 16,384 UTF-16 code units per element and 65,536 per
message; at most 100 elements are projected. IDs are at most 256 code units.
Incoming diagnostic enum values are not interpreted as supported send features.

Queueing is bounded to 100 pending event batches and 100 subscribers. Excess
queued batches are dropped with a diagnostic counter, not persisted. This is
not a durable stream and does not promise lossless replay. Subscriber throws or
promise rejections become sanitized `listener_error` events; the originating
subscriber is excluded, and error-event failures do not recurse. Unsubscribe /
dispose suppresses queued delivery. Readiness must also be exactly true when a
native callback is received; inactive callbacks are ignored.

## Outgoing text-only schema

```javascript
const acknowledgement = await messages.send({
  peer: { chatType: 1, peerUid: explicitlyObservedNativeUid },
  elements: [{
    elementType: 1,
    // elementId: explicitlySuppliedNativeElementId, // optional, never invented
    textElement: {
      content: 'hello', atType: 0,
      atUid: '', atTinyId: '', atNtUid: '',
    },
  }],
  timestamp: explicitlySuppliedTimestampString,
});
```

All five text fields are mandatory. Empty text/mention IDs are allowed **only
when explicitly supplied**. No mention type, UID, UIN, timestamp, guild ID or
element ID is synthesized. Peer requires explicit `chatType` 1 or 2 and
`peerUid`; `guildId` is optional, forwarded exactly if supplied, and is not an
advertisement of guild-chat support. Unknown outgoing fields or element types
are rejected before allocation. Caller inputs are descriptor-copied into
immutable bounded snapshots before asynchronous work.

Every send checks `isReady() === true` immediately before allocating an ID and
again immediately before dispatch. The adapter calls
`generateMsgUniqueId(chatType, timestamp)` **once**, requires a string result
without `Number` coercion, and then calls
`sendMsg(msgId, peer, elements, new Map())` **once**. No time is guessed and no
internal/public network API is used.

Only a real promise resolving to `{result:0, errMsg:string}` returns:

```javascript
{ state: 'accepted', native_msg_id: 'full-native-string-id', result: 0 }
```

Strict `result: -1` returns `state: 'rejected'` with `result: -1`. Acknowledgement
objects are frozen. Neither state means delivered; no delivery event is
fabricated. Native `errMsg` must be bounded textual data, but is intentionally
not returned, attached as a cause, or logged (it may contain private content).

A throw, rejected promise, timeout, non-promise return, invalid generated ID or
invalid acknowledgement throws a sanitized `NativeMessageError` with
`code: 'SEND_UNCERTAIN'`, `state: 'uncertain'`, and `native_msg_id` when known
(otherwise null). Timeout is default 30 seconds, explicitly configurable via
`sendTimeoutMs` from 1 to 300,000 ms. Timeout does not cancel a native send; a
late acknowledgement may still occur. Losing readiness after allocation but
before dispatch throws `NOT_READY` without dispatching.

There is **no automatic retry**, no journal, no fake persistence, no idempotency
promise across independent sends, and no delivery reconciliation. A future
caller-injected journal must be designed explicitly before retries or durable
claims can exist. An uncertain send must not be blindly retried.

## Offline verification

```text
python -m pytest tests/test_qq_native_messages.py -q
node --check qq_transport/native_messages.js
```

Fake-native tests inspect dormant behavior, strict readiness, exact callback
names/batches, full-width string IDs, explicit peer/mention fields, getter-safe
projection, immutable snapshots, sanitized callback errors, bounds, exact
fourth-argument Map, strict status acknowledgements, timeout/throw uncertainty,
no retry, lifecycle recheck and listener-token type/process policy. These are
contract tests only and must not be described as an authenticated QQ session or
real native message verification.
