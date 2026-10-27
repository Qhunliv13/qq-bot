# QQ bot architecture and source-aware cross-group memory

## Runtime data flow

```text
QQ / NapCat native event
  -> AstrBot admission + immutable sender/message/quote records
  -> per-scope pending batch + persisted history
  -> current-scope stable history + current messages
     + CrossScopeMemory other public-group overviews
     + per-scope tool observations / task state / attachments / fresh clock
  -> one native model/tool loop (tool_choice=auto)
  -> tools: QQ / VM-browser / public web / workspace / workers / durable tasks
  -> shared output decoder + native reply/mention/media transport
  -> confirmed visible history and durable task delivery outcome
```

The execution lock serializes shared desktop/model/tool/send work across groups. Due tasks
join the same FIFO queue without preempting in-flight chat. Different scopes retain separate
pending/busy/history/observations;one shared persona is not a grant to copy private material.
This is a single bot runtime with scoped state,not separate independent group agents.

## Memory layers and storage

|Layer|Authoritative shape and purpose|
|---|---|
|Current input|Native message records:scope,message ID,sender ID/display name,body,mentions and quote provenance. Actual sender IDs determine owner role;quoted names/other-group claims do not.|
|Current short-term context|Per-scope in-memory recent history cap2000;stable chronological120–239-entry window,before current message append. Existing history logic remains unchanged.|
|Durable chat history|`/root/higan/history/<scope>.jsonl`,existing `ts/line` retained;new received messages additionally persist exact native `record`. Legacy text is not reverse-parsed into invented identity.|
|Cross-group overview|`CrossScopeMemory` discovers persisted numeric group scopes,loads20most recent source rows per other visible group,shows coverage/has-more. Sorted scopes;no generated summary or trust promotion.|
|On-demand recall|`history_search` uses the same scope selector,searches full stored lines/native record data and preserves source,timestamp,message/sender/quote metadata. It does notglob private history when scope is empty.|
|Long-term saved facts|`/root/higan/memory/memory.json`,existing global/scoped structure and2000fact cap. New `remember` stores actual source scope and message records with untrusted/model-saved provenance. Full visible list is searched before applying result limit.|
|Tool evidence|Per-scope archive under`/root/higan/tool_observations`;actual attempted tools/results,not chat-memory facts or authorization. Same source-separated context envelope.|
|Tasks/worker state|Separate durable task,delegation,plan andjob stores. Task IDs are internal identifiers,NEVER native message IDs;status/prose does not prove delivery.|

20rows per other group is an explicit context overview budget,not deletion of stored history.
Detailed questions can use history_search;records outside the overview remain on disk.
This is not a persistent search index or vector database:current JSONL readers still scan
selected history files,so disk cost grows with retained history. No latency speedup is claimed.
After reload,cross-group context is reconstructed from persisted scopes rather than the old
volatile global120-line buffer. Bot replies are also persisted as rendered lines;where their
native sender/receipt metadata was not stored,the system does not invent it.

## Corrected issues

1. Old `_all_recent` collected every input as`[群<scope>]`,including`private:<QQ>`,then
   other_scopes only excluded the current scope. Private input could enter other groups.
   The new shared selector includes numeric public groups only;own private history is
   available only when explicitly read from that same private scope. Muted groups are
   excluded from new automatic shared views. Mention-only remains a reply gate,not a
   claim that it is an existing confidentiality ACL.
2. Old global buffer was input-only,max120,mixed acrossgroups andnot reloaded from disk.
   One noisy group displaced others. New bounded per-group durable views retain continuity
   and explicit provenance without flattening display-name strings into speaker authority.
3. Old history_search scanned arbitrary first30JSONL files,includingprivate files,and
   truncated returned lines to300characters. The shared read path searches explicit visible
   scopes andreturns full stored source rows;legacy stored line truncation is not reversible.
4. Old facts_search filtered only the first500facts,despite a2000fact store. It now filters
   the entire visible fact set before applying the caller's result limit. New facts are no
   longer forcibly truncated to200characters by the dispatch layer. Dedup preserves the
   first origin,never merges private identity evidence into a public copy.
5. Old remember marked every source as`tool`. New writes retain source scope+actual current
   records. Legacy unknown labels remain explicitly unknown;no migration fabricates sources.

## Quote incident and transport

Actual native message1099136016 at2026-10-27 12:20:41UTC+8 displayed
`[quote:2182434b]...`. It contained text only,no native Reply orAt. `2182434b` was a saved
task ID;the task completion note persisted the same body. The incident predates the
12:47native readback release. Old independent parsers accepted decimal IDs only,so the
hex task identifier remained visible.

One shared lexical quote helper now consumes raw LEADING transport controls and separates
visible body from quote metadata. Invalid/hex/task identifiers never become guessed QQ
quote targets anddo not leak as leading control syntax. Valid legacy native decimal IDs
remain supported. Structured reply_to is authoritative. Inline,quoted,backtick and fenced
examples stay literal. No business keywords,group IDs,task names or canned wording are used.
The sender uses the shared helper rather than maintaining a second regex implementation.

## Boundaries and honest limits

- Other-group material is untrusted attributed evidence,not current human input,an owner
  grant,a tool outcome or an instruction with elevated priority.
- This fixes automatic context andstandard history/fact retrieval paths. General-purpose
  filesystem/VM tools are still capabilities,not a security sandbox. Existing global facts
  with unknown legacy origins are not retrospectively guaranteed private-safe.
- Existing fact edit/delete policies are unchanged;no new owner authorization scheme is
  implied. No unrelated group mute/mention permissions or persona were changed.
- Existing history lines were capped2000characters atwrite time. New native record bodies
  preserve source data where available;old lost text/IDs cannot be reconstructed.
- `history.py`'s historical5000line constant was not an enforced retention mechanism;do not
  claim files are capped or automatically summarized. No archive was deleted by this work.
- Architecture still has a large orchestration main.py;this change adds reusable source/
  visibility/decoding boundaries,not an unreviewed wholesale runtime rewrite.

## Verification

1887pytestpassed,24platform skips,25subtests;syntax,pyflakes and release validation clean.
95tool route/provider contracts remain passing offline. New actual-dispatch fixtures prove
restart provenance,foreign-private rejection,public source preservation,full-history search
outside bounded overview,valid nativeReply and malformed-control/literal-example handling.
No synthetic QQ message,model-based memory rewrite or automatic history migration is used.
Deployment uses exact baseline hashes,backup,committed bytes and native plugin-only graceful
reload. See the separate deployed receipt for exactcommit,health andpermission cleanup.

## Prior 13:00 task follow-up (read-only)

The opening task was claimed13:00:24.261415 andcompleted13:01:33.123692. Native @message
1204114318 at13:01:03 targeted the actual requester;the VM native API response contained the
same messageID. The task's own last_outcome instead records its later summary178916260,
so those two IDs must not be conflated. The60s fixed-rate ETF task resumed;native broadcast
350753333 at13:04:52 matches qq_send delivered:true. Shared queue/model latency means these
are not a promise of exactwall-clock one-message-per60s. Audit sent no messages or retries.
