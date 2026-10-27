# 2026-10-27 scheduled tasks, delivery proof and tool audit

## Incident facts (Asia/Shanghai)

Native persisted tasks and read-only group history were correlated, not inferred from
model prose. The ETF task was created11:28:12 and ran three model/tool rounds, including
three quote fetches and sends at11:29:39,11:32:08,11:34:48. The water reminder was due
11:40:19,claimed11:40:30,and attempted qq_send11:40:46;its task_done ran11:41:04.
None of those attempted sends had native message-ID receipts or matching group messages.
The VM helper posted an action envelope to OneBot's ROOT endpoint instead of/action.
It received status=ok/retcode=0/data={}/message='NapCat4 Is Running',then falsely returned
ok:true. The task loop and model interpreted tool success as delivered. The11:33'two
rounds broadcast' and11:41'already reminded/@ed' statements therefore lacked delivery proof.

The model's later workaround was real: vm_exec called/send_group_msg with native arrays,
producing actual message IDs1454285563,1495865756,244562165 matching native at+text group
messages. No VM API source edit was observed; this was an endpoint/payload workaround,
not a persisted repaired function. The medicine reminder11:56:18 also had a verified
native @ receipt. Preserve the working native format; do not roll back or claim no @ works.

The saved13:00 task is active/idle with an origin but was scheduled12:59:19.734310,
40.266seconds early,from a model-calculated relative delay. A reviewed correction must
use exact time,compare-and-set against this snapshot,and leave all unrelated tasks intact.
No retrospective duplicate water reminder or ETF burst is authorized by this audit.

## Generic fixes

- qq_send uses the existing correct native/action bridge directly, structured at/text arrays
  and strict positive message-ID receipts. Literal CQ remains text; no heuristic CQ parser.
  Missing/health/boolean/null responses are unknown,not success. No mutation auto-retries.
- Typed mentions adds QQ-ID lists to the existing tool,not a special-case command. Foreground,
  tasks and model tools retain one sender implementation and destination-policy checks.
- TaskEvent reuses the installed AiocqhttpMessageEvent._parse_onebot_json serializer and
  sends once to the saved,validated scope via/action. It requires a native receipt,not the
  framework routing boolean;stale receipts reset before each send.
- A per-attempt delivery ledger saves attempted/confirmed/failed/unknown and receipt IDs.
  Failed/unknown sends block task_done and cause paused/review rather than fictitious done.
  Intentional SILENT is still allowed for genuinely quiet tasks;no forced final replies.
- run_at supports absolute Unix seconds with explicit timezone conversion by callers;
  conflicting delay is rejected. Optional fixed_rate preserves original phase,skips missed
  ticks and never overlaps. Existing after_completion timing remains backward-compatible.
- Due tasks enter the same FIFO execution lock instead of being perpetually skipped whenever
  chat is pending. Current foreground work is never preempted. Polling/model/queue time still
  causes lateness;this is not a hard-real-time scheduler or promise of one message every60s.
- Persisted task creation requires current authentic origin+matching scope. It reports actual
  status,scheduler_accepted and scheduled_at;updating paused work does not falsely promise execution.
- Native vm_recall and document uploads also bypass the same broken VM root route. Recall
  selects only the logged-in bot's own recent messages. Uploads stage an isolated file into
  NapCat and require native upload acknowledgement;accepted upload is NOT a send-message ID.
  Media uses the existing plugin handoff;no replacement service or VM restart is introduced.
- Task store public transactions include POSIX flock sidecars,atomic file replacement and
  existing thread locking;reviewed operational deadline corrections cannot race normal updates.

## Tool validation and limits

95 unique native tool schemas. The audit now executes real dispatch/provider-pairing code
for all95;native QQ is no longer replaced by a generic success stub. Root welcome/empty ack
mutation tests fail as required. Scope/type/media/task/delegation/local-state behavior is
covered offline;transport effects are simulated where testing would message users,delete,
recall,change profiles,authenticate mail or manipulate a live desktop.

95effect inventory:workspace8,discovery/skills5,delegation5,tasks/plans10,VM/browser/mail26,
QQ/social/delivery/files/forwards14,autonomy2,public web4,memory/history/policy21.
Do not describe these as95 physically executed live actions.

Fresh12:25–12:26 read-only live probes passed native status/login/version/group-list,
VMhealthz(ok/x_ok/cdp_ok),VMfile-list andCDPversion/targets. Native7groups,CDP3targets.
Installed serializer signature/source matches the reused API. An existing screenshot HTTP200
transfer was incomplete over the SSH diagnostic channel,so screenshot byte delivery is NOT
certified by that probe. No active screenshot,QQ test send,recall,profile or credential mutation
was performed. Historical receipts additionally cover web/image discovery/workspace/worker
capabilities,but do not substitute for current physical validation of every tool.

## Deployment rules

All code committed before controlled baseline hash precheck→backup→atomic distribution→exact
plugin native graceful reload→health/model/native-read probes→temporary access revocation.
No service restart,foreground cancellation,billing/channel change,owner impersonation or
production ad-hoc source patch. A reviewed task deadline adjustment is a separate recorded,
idempotent operational action through the locked public task API,not a source-code patch.
See the deployment receipt for exact commit,test totals,deadline correction andlive state.
