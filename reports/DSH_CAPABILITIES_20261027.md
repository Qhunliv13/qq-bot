# DSH-style capabilities and genuine autonomous work opportunities

## Observed baseline, not model-intent speculation

Read-only audit at2026-10-27 08:12:21UTC+8: advertised71tools;24h548model responses,
111tool requests,12names observed in completed-turn logs.12h238responses/86requests;
86timestamped archive records matched requests,46success/40error.59of71tools absent
from24hcompleted-turn-name observations,not proof they have never been called.
53empty-current-batch prefix observations had zero next-response tool calls; they matched
53autonomous completed logs numerically but no common turn ID exists,so attribution is
correlated,not uniquely joined. Autonomous cadence600seconds,known4/eligible3scopes.

Failure categories:vm_exec23(16RemoteDisconnected,1URLError,6nonzero exits),job_status17
(14distinct cancelled jobs,2failed jobs,1transportURLError). Explicit30second timeout1;
no invalid returned JSON evidence. Cancellation is not necessarily a broken tool. Connection
disconnect causes remain unproven. This release does NOT claim to repair all old VM errors.

## One additive native catalog:71 to92

-8workspace tools:read,write,edit,glob,grep,cwd,present,read_image,using existing VM exec.
-5discovery/reference tools:tool_search,tool_describe,skill_list,skill_read,skill_save.
-5real model workers:start,status,list,message,interrupt,using bot's own provider.
-1background-script job_list,sharing existing job ownership and status implementation.
-2revisioned persistent task-plan tools:read/update,stored in existing tasks.json.

A fixed full native schema remains stable across ordinary requests; discovery returns actual
schema snapshots,not fake tool declarations or a per-turn changed prefix. Each declaration
has an explicit dispatcher/route audit. Worker model requests use separate deep-copied
read-only schemas; parent catalog is never monkeypatched. Tool_choice remainsauto.

Workspace operations run on the bot VM,not this DSH session,GUI,Windows host or relay host.
UTF8read/write/edit use persisted observation hashes,explicit CAS and atomic replacement.
Long output has explicit pagination/truncation. Glob includes hidden/ignored files; grep
uses Python re/fnmatch,not a claim of ripgrep syntax. Present returns references only and
does NOT automatically upload to QQ. Image observation appends to existing native vision
transport. Directory capabilities/existing vm_exec are NOT a security sandbox.

Skills are isolated reference files with path/symlink checks,CAS and authorized save;
their text cannot grant authority,alter tool protocol or execute on load. No system persona
rewrite. Native worker contexts/results/inbox/receipts are independent and scope-bound;
workers have13explicit read-only tools,not shell/browser/QQ mutation or recursive delegation.
Running inbox updates join between model requests; they do not cancel in-flight requests.
Cancelled/failed/orphaned states remain typed. Worker results never masquerade as user IDs.

## Actual opportunities, not compulsory tool calls

Autonomous context now includes actual catalog categories/names,existing scoped unfinished
tasks,workers,skill names and recent error metadata; unavailable sources remain explicit.
No invented human wake message,no instruction to use a tool every turn,no forced speech.
A completed worker queues a deduplicated durable activity_result opportunity; the existing
controller consumes it once,with authentic destination and current enabled/muted/mention
policy. Cadence/model schedules are not reset. Results remain discoverable by agent_status.
Task plans attach to existing goals and do not create a competing scheduler or mark goal
completion merely because the checklist is complete. Autonomous history uses the existing
shared stable chronological window,not the full2000entry buffer every interval.

## Scope honesty

This is functional native parity for the reviewed capabilities,NOT embedding the current
DSH GUI runtime or claiming that every DSH facility is available. DSH-only GUI approvals,
external connectors/credentials,unrestricted write subagents,session fork/resume,full cron
andtimezone reminder syntax,and large orchestration workflow DSL are not implemented here.
Existing once/every tasks and model-selected next_at schedules remain available. No new
model/tool-round/time caps,forced action chains or permission bypasses were introduced.
Public profile/Qzone mutations are still not live-tested by this release.

## Verification and rollout

Full test counts and deployed commit/hashes are recorded in the release receipt. Contract
audit uses all92realnative routes,actual generated VM helper programs locally,paired IDs,
retained observations,successful result assertions and worker cancellation/isolation checks.
Windows skips genuine POSIX processes/symlinks explicitly. Actual Linux VM candidate probe
executes write/read/hash-edit/grep/glob/job_list plus native real-model worker,and writes
only isolated probe files/receipts; no QQ messages,signature changes or Qzone publication.
Deploy only committed bytes,baseline hashes,backup,exact-plugin native reload,live probes,
process/policy verification and temporary access revocation. No service restart.
