# Independent QQ transport core — native backend not implemented

These independently authored modules contain no NapCat imports, endpoint proxy,
loader or copied handler implementation. They do not log in to QQ, load Tencent
native libraries, open HTTP/WebSocket listeners, or replace the running transport.
Importing the package does not start any service or install default handlers.

## Implemented and tested offline

- `store.py`: explicit local SQLite operation journal. A mutation is claimed
  atomically per account and operation ID before its handler can run. Identical
  completed requests replay the saved response; conflicting payloads fail. Pending
  or unknown requests never expire into an automatic retry. Request parameters are
  persisted only as a canonical-JSON SHA-256 digest, while responses are private data.
- `actions.py`: strict, explicitly injected async handler dispatch with account,
  action, parameter and echo validation. Mutations require an operation ID. Native
  acknowledgements are validated, not guessed: `ok`/0 means accepted, not necessarily
  delivered. Unsupported actions have no fallback to another backend. Generated
  aliases must be explicitly registered; stripping suffixes is not feature parity.
- `events.py`: immutable local event outbox, account isolation, ordered per-consumer
  acknowledgement and a serialized pump into an injected async sink. Native payload
  fields remain intact. Internal sequence IDs are not QQ message IDs.

## Guarantees and limits

**Mutations:** at-most-once handler invocation per claimed operation key. If the
process crashes after claim but before execution, the request remains pending rather
than guessing that execution is safe. This can sacrifice liveness in favor of avoiding
a duplicate effect; a real native status-reconciliation mechanism is still needed.

**Events:** delivery is at-least-once across a send/acknowledgement crash. The same
stable event ID can be redelivered; a sink must deduplicate it. A plain reverse
WebSocket send is not an application-level acknowledgement. The current event pump
must not be described as an implemented AstrBot reconnect/acknowledgement protocol.
One pump instance serializes calls; multiple processes claiming the same consumer
are not supported by this module.

**Protocol:** requiring mutation operation IDs is an intentional internal safety
boundary, not proof of drop-in compatibility with clients that omit them. No adapter
is installed in production. The complete installed action declaration inventory
contains 180 base actions plus 360 aliases, but **zero real native handlers have been
implemented or verified** in this core.

**Storage:** use dedicated, private local database paths; never point these constructors
at QQ login stores, bot production databases or account directories. Response and
event content may be private. Encryption, retention policy, cross-machine storage
security and migrations are not supplied by these initial components.

## Offline verification

```bash
python -m pytest tests/test_qq_transport_store.py tests/test_qq_transport_actions.py tests/test_qq_transport_events.py -q
```

Tests inject synthetic handlers and sinks and temporary SQLite paths. Their success
does not establish QQ native ABI compatibility, login, message sending, event ingress,
HTTP/WebSocket compatibility, authentication, all-feature parity or migration safety.
See `docs/QQ_TRANSPORT_REWRITE.md` for the remaining gates. Existing production NapCat
must remain unchanged until independent end-to-end preservation is demonstrated.
