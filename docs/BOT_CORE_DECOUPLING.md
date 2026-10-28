# Independent bot core; QQ as an attached capability

## Decision and current status

**Feasible design direction: separate the bot's existence from any QQ connection.**
The bot owns its identity/persona, memory, tasks, goals, reasoning, tool execution and
operation evidence. QQ is one externally connected capability and delivery endpoint.
QQ login loss should affect QQ input/output and QQ-native operations, not erase or
stop unrelated work. This is the target behavior, **not a claim about the current bot**.

Keep the existing QQ account, all existing functions, data, configuration and behavior.
Do not replace/delete NapCat or change the production adapter. Even after equivalence
passes, a cutover requires a new explicit user instruction. This design does not
introduce a more directive persona or force public replies.

Two related projects are distinct:

1. **Independent QQ backend:** bootstrap, session, native operations and OneBot-compatible
   transport without NapCat. Native initialization and all-feature parity remain unproven.
2. **Platform-neutral bot core:** move existing orchestration behind neutral input,
   identity, capability and output boundaries. This does not require a completed new QQ
   backend: the existing QQ adapter can remain the first connector during development.

Replacing NapCat alone would still leave a QQ-centric bot. Decoupling the core alone
would still leave NapCat as an implementation behind the QQ connector. Neither task
should be called completion of the other.

## Target component ownership

```text
                    User / operator
                 local console or UI
                         |
       +-----------------v------------------+
       |             Bot core               |
       | identity/persona, memory/goals      |
       | scheduler, reasoning, tool routing  |
       | shared FIFO execution coordinator  |
       | durable task and operation evidence |
       +-----------+-------------+-----------+
                   |             |
            capability ports   output intents
                   |             |
       +-----------+-------------+----------------+
       | local / files / web / QQ / future adapters |
       +------------------------+------------------+
                                |
                     QQ connector boundary
               existing backend or new backend
                                |
                        Personal QQ account
```

The local operator interface is the independent control surface, not another QQ group.
A Web UI, CLI or local API can host it; choose one in a later implementation step rather
than silently deploying a new network listener. The core must have a dedicated health
state separate from each connector's login/online health.

| Layer | Owns | Must not own |
|---|---|---|
| Core identity | Bot persona and operator principals | QQ nickname as bot identity or authority |
| Memory | Source-attributed facts, histories, observations | Cross-platform/private merging by display name |
| Orchestrator | Existing turn/tool pipeline and shared FIFO | One competing pipeline per connector |
| Task/goal engine | Intent, execution state, continuation, dependencies | A mandatory live QQ event for every task |
| Capability registry | Declared/available/verified operation status | One global online flag that enables all tools |
| Output router | Durable intent and observed outcome | Treating a routing boolean as delivery |
| Connector | Authenticated native IDs, codecs, native receipts | Persona, global memory policy, privileged-owner inference |
| QQ backend | QQ login, native lifecycle/events/actions/media | Independent bot's reasoning or autonomy policy |

## Actual coupling to unwind

Current implementation evidence includes:

- `plugin/main.py:157` implements the bot as an AstrBot `Star`; the framework is its
  host today, not a standalone core runtime.
- `plugin/main.py:177,1302,3048` and `plugin/autonomous_turn.py` share a single execution
  lock. Preserve that one FIFO across foreground, tasks and autonomy; channel-specific
  locks would alter ordering and permit conflicting writes.
- `plugin/main.py:733-786` builds context from event objects, scope and QQ-owner matching.
  Keep the neutral context builder and source provenance, move authentication/authority
  mapping to verified principals without weakening the existing owner boundary.
- `plugin/task_event.py:27-30,55-79` encodes group/private QQ scopes and uses AstrBot's
  installed OneBot serializer. Do not write a competing renderer while extracting core.
- `plugin/main.py:3040-3042` and `plugin/autonomous_turn.py:67-69` require stored origin
  for work today. A missing QQ origin currently does not imply autonomous local execution.
  Neutral task execution and later QQ delivery must be designed explicitly.

Additional persistence/authority findings matter for a safe extraction:

- `plugin/history.py:36-39` sanitizes filenames and truncates scope labels to 60
  characters. Do **not** feed the new canonical scope key directly into this legacy
  path function: collisions/truncation are possible. Keep an explicit sidecar mapping
  to original scope/path, retain original record IDs and do not guess missing accounts.
- `plugin/cross_scope_memory.py:26-52` admits numeric public groups and tightly scoped
  private records. `plugin/memory.py:281-286` retains original private provenance even
  after public restatement. A new resolver must not bypass these visibility checks.
- `plugin/tool_context.py:190-194` hashes old scope strings for observation paths.
  Tasks and autonomy also persist origins/scopes. Mapping only history filenames is
  insufficient: observations, facts, task claims and pending opportunities need the
  same explicit compatibility view, without destructive rewriting.
- `plugin/main.py:146-149,2218-2229,4205-4207` uses a fixed QQ-owner set for authorization.
  There is no established synchronization with a framework owner configuration. An
  injected single AuthorityProvider must drive both role display and enforcement;
  introducing an independent second owner list would create inconsistent authority.

Reuse existing pure modules where possible. Extraction must preserve their behavior,
not copy each module into a new implementation and maintain divergent versions.
`plugin/agent_loop.py:88-115` already accepts LLM/tool/observation callbacks and is a
promising extraction seam. `conversation_context`, `conversation_state`, publication
selection and task planning are further candidates. `autonomy.py` has no AstrBot import
but still only accepts QQ numeric scopes; absence of an import is not proof of neutrality.
Detailed code references are an inventory snapshot; recheck them before moving code.

## Identity and session model

Use separate identities for the bot, an external account, a sender and a conversation.
A conversation key must include **platform + connected account + kind + native ID**.
The same numeric ID on QQ and another connector, on two bot accounts, or in private and
group contexts is not the same conversation. Encode keys unambiguously, not by joining
unescaped colon-delimited IDs. Keep native IDs as strings; do not invent universal
numeric identifiers or silently remove leading zeros.

A principal is authenticated by its connector/control boundary, not by message text,
quoted metadata, display names or an LLM claim. Cross-platform owner bindings require
explicit verified configuration. Do not automatically promote an existing QQ owner to
an owner of a different connector just because a name or numeric ID matches.
Tasks/system events do not impersonate humans. Attachments, quoted/forwarded content
and tool results remain attributed data, never a source of privileged instructions.

Existing histories and identifiers must remain readable. Add a mapping layer for old
QQ scopes; retain original records and native IDs. No destructive database rewrite,
renaming of legacy IDs, or automatic merge of private history into public shared memory.
The current globally chronological public-group memory policy must be preserved for
its authorized QQ scope set; additional platforms are not automatically admitted.

## Tasks versus delivery

A task has separate **work state** and **publication state**. These are examples of
component states, not new policy forced onto the existing bot:

- A local file/web task can execute when QQ is offline if its actual tools and authority
  are available. Its result is retained in the core, even if QQ publication is pending.
- A QQ history/moderation/send task needs the QQ capability. Disconnection is a real
  unavailable dependency, not permission to fall back to another destination or identity.
- If QQ goes offline after work completes, preserve the output intent for its exact
  destination. On reconnect, do not blindly replay all old output or flood the group.
  Reconcile durable state and original task timing before admitting publication.
- A send with unknown outcome is not retryable just because a connector reconnected.
  Preserve existing confirmed/accepted/failed/unknown distinctions and operation IDs.
- Local inspection of a pending result is not equivalent to having sent it to QQ.

A durable outbox is useful, but the present `qq_transport/events.py` outbox is for source
**events**, not a ready-made user-message publication system. Do not repurpose it and
claim exactly-once delivery. Existing explicit publication/new-message rules, sibling
observations, quoting/forwarding provenance and media receipt handling must remain.

## Capability and tool behavior

Separate **declaration**, **connection availability**, **authorization** and **verified
behavior**. A connector can be online while one action is unsupported; a registered
mock handler does not prove native capability. Keep all 106 current tool schemas and
the installed QQ feature baseline; do not equate their counts.

Route platform-specific operations through a connector capability port while local,
web, file and memory operations remain independent. Only actually unavailable tools
should report unavailable; do not abort the entire core on a QQ health check. Do not
hide errors with zero/empty success results, spoof receipts, silently change scope, or
retry ambiguous mutations. Existing owner/write-policy checks remain authoritative
until an explicitly verified replacement is tested.

## Incremental implementation and validation

1. **Neutral contracts in isolation:** endpoint, principal and input provenance with
   collision-free scope keys; preserve unknown native content as data. No production
   handler is moved in this step and no independent engine is claimed.
2. **Compatibility adapter:** translate the existing AstrBot QQ event into neutral
   input and back through the existing serializer/receipt path. Compare snapshots and
   ordering using recorded synthetic fixtures, not live account-data copies.
3. **Core extraction:** relocate, not duplicate, one existing turn pipeline behind
   injected model/tool/history/output ports. Both old and experimental adapters must
   call the same implementation. Keep a single execution coordinator.
4. **Independent task host and local control:** use the extracted pipeline without an
   AstrBot/QQ event; test local tasks with QQ disconnected and retain unavailable QQ work.
5. **QQ backend validation:** continue native bootstrap and complete real feature/event/
   media coverage independently of the orchestration extraction.
6. **Equivalence and rehearsal:** history/provenance, authority, FIFO, tools, scheduled
   work, publications and restoration tests; no production switch without user instruction.

Acceptance examples: same external IDs on different connectors do not collide; private
records never enter unrelated context; task events cannot impersonate owners; QQ loss
leaves local task results intact; unknown sends never auto-retry; adapters cannot create
new owner authority; no data or tool disappears during incremental extraction.

## Explicit limits of the first code increment

The new `bot_core` contracts are an isolated boundary prototype, not an extracted bot
engine. They validate routing/provenance shape but cannot authenticate an untrusted
caller by themselves. The current production bot remains hosted by AstrBot and QQ;
local scheduling, independent execution and output routing are still design/next steps.
There is no new listening API, production migration, credential copy or live cutover.
