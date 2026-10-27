# QQ applications and explicit public-reply completion

## Public replies

`reply_now` accepts an optional strict boolean `finish`. The default false continues
the same native tool loop and permits further updates. A true value completes the
turn only after nonempty delivery is confirmed and the entire current tool batch
and its observations are recorded. No further model completion is requested.
Arbitrary tool JSON cannot finish a turn: the parent supplies a trusted predicate
checking the actual tool name, original boolean argument and confirmed receipt.

This makes a completed public answer terminal without filtering particular words,
limiting reply count or replacing model prose. A continuing model can still choose
a later reply or silence. It is not an unconditional ban on every possible future
status-only sentence when the model elects to continue.

## Application tools

- `qq_request_capabilities`: installed contract and owner eligibility, not a live probe.
- `qq_requests`: owner-only list of captured native friend/group application events.
- `qq_request_decide`: explicit accept/reject by a captured opaque local request ID.
- `qq_join_group`, `qq_answer_group_question`, `qq_add_friend`: declare the requested
  intent but return `native_capability_unavailable` on this installed API. They do
  not submit requests, answer challenges or misuse approval APIs as active joins.

Current installed backend is NapCat4.18.37. Registered `set_group_add_request` and
`set_friend_add_request` resolve existing native flags. The group contract accepts
flag, explicit approve, optional reason and native count; it does not use subtype.
Friend accepts flag, explicit approve and optional remark. Their successful native
response proves submission/acceptance only, not group/friend membership. The friend
native handler does not await its underlying approval promise; acceptance cannot
be strengthened into verified friendship.

No proactive group join, entry-question answer or proactive friend-add action is
registered in the installed bundle. `set_group_add_option` changes settings of an
owned/administered group and is not an entry-question answer API. An installed
suspicious-friend request handler ignores its approve argument, so it is not exposed
as a reject operation. Official API/event descriptions are corroboration; installed
registrations and handlers determine this runtime's capability.

## Request sources and permissions

AstrBot's native adapter converts OneBot request events into ordinary queued event
objects. The plugin captures native `post_type=request` data before chat/group
filters, then returns without treating it as an empty human message or invoking a
model. Incoming requests never authorize their own acceptance.

SQLite stores raw opaque flags privately. Public tool rows omit flags and retain
kind, requester, group/subtype, native time, comment and account/platform provenance.
Comments are untrusted source data. A composite source key avoids cross-account flag
collisions; duplicate events keep the original native metadata. Only actual owner
sender identity can list or decide; an autonomy event or sender claim cannot grant
permission. Known request accounts must match the authenticated current bot account.

Two durable claims bind both the operation ID and request ID. The store is marked
unknown before the side effect, so a crash/timeout cannot become an automatic retry
with a new operation ID. Accepted receipts remain explicitly not membership-verified.
No request tokens, native errors or incoming private comments are logged/publicized.

The request list covers events captured while this implementation is active. There
is no registered normal friend-backlog list API. Group system-message IDs are not
converted into raw flags by guessing; an empty captured list is not proof that the
account has no prior pending requests.

## Evidence and validation

A read-only screenshot audit found a real answer sent through `reply_now`, followed
by a second native message containing only a completion acknowledgement. The second
body differed from the first, so exact-body deduplication correctly did not match it;
this is a turn-completion protocol issue, not a special phrase-filter issue.

All native tool routes and provider payloads have offline contract coverage. The
catalog contains101 unique tools: the audit distinguishes98 simulated acknowledged
receipts from3 explicitly unavailable capabilities. This is not101 successful live
external operations. No live QQ applications, approvals, joins or friend-adds are
performed for deployment verification. See deployment receipt for exact test totals,
commit, manifest hashes, native graceful activation and health checks.

Primary references:
- https://napneko.github.io/onebot/api
- https://napneko.github.io/develop/event
