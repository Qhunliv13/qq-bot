# One shared memory across all groups

User explicitly requested direct group-memory integration instead of per-group overviews.

## Effective behavior

- Every public group turn reads the SAME chronological persisted group-memory timeline.
  Current and other groups are no longer separate history/overview blocks. other_scopes
  remains empty for compatibility;conversation.history holds source-attributed shared rows.
- A single global240row context budget replaces per-other-group20row quotas. This bounds
  prompt material,not stored memory;full history remains searchable. No archive is deleted.
- Each row retains origin_scope,scope,timestamp,line andavailable native record metadata.
  Source tags do not create separate memories or prevent cross-group recall;they avoid
  inventing who spoke,confusing quote origin orpromoting owner claims into authority.
- Current batch stays separately attributed andis excluded from prior shared history only
  by exact(scope,native message ID). Same message-ID text in another group is not removed;
  legacy records are not parsed for imagined IDs.
- All public groups participate,including muted groups. Existing mute/mention-only rules
  still govern sending. Default standard history/fact retrieval likewise includes allgroups;
  an explicitly supplied search scope can still narrow a query.
- Known private histories/facts stay private to their own scope. This request unifies group
  memory,not private chats. Existing unknown-source legacy facts are not retroactively proven
  private-safe;general-purpose VM/filesystem tools are not a privacy security sandbox.
- Shared long-term facts remain one store with source references. No per-group fact silo,
  business-specific summarizer,new vector database,per-group schema orforced model tool use.

## Data path

QQ event -> durable per-origin source records -> one merged global group timeline
        -> same model/persona + native tool loop -> current destination policy -> native send.

Per-origin JSONL files remain append-only evidence storage,not semantic memory boundaries.
The merged timeline is a read view over them;reload reconstructs the same shared memory.
The execution lock,task delivery proofs,quote-boundary fixes andmodel parameters stay intact.

## Validation and limits

Tests exercise actual native context/provider payloads from differentgroups,the same shared
history,source fidelity,global budget beyond20rows,muted-group recall without muted sends,
private isolation,current-batch deduplication,legacy missing timestamps anddisk persistence.
One global context budget is not unlimited model memory. Keyword search still scans stored
files;no persistent index or latency improvement is claimed. See deployed receipt for exact
commit,complete pytest totals,live source metadata andgraceful activation status.
