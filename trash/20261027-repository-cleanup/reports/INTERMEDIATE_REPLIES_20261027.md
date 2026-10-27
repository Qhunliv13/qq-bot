# Optional multi-message replies inside the native tool loop

## Actual user task status

Read-only evidence at2026-10-27 10:12:25UTC+8: user SVG/cycling drawing request received
10:05:58. First turn ended10:09:59 after a provider failure10:09:49; subsequent empty-current
opportunity began10:10:37. Actual successful tool observations include image creation,
workspace_read_image twice andsend_shot10:12:16.503 withok=true/delivered=true. Turn ended
10:12:23. SVG1531bytes and final PNG75416bytes were confirmed; visual correctness and an
HTML file were not verified.22managed script jobs were historical terminal,none running.
This is not proof that the entire bot/model is idle or the recipient visually saw the image.

## Generic capability

Added one native reply_now tool: the model chooses public text and optional current-batch
reply_to IDs. The existing normal sender enforces silence,quoting,policy,cleaning,media and
history; control then returns to the SAME native tool transcript. A tool receipt records
actual delivered body or unconfirmed failure. No second independent model rewrites text.
No fixed acknowledgments,topic keywords,phase templates,minimum count,polling chatter or
compulsory progress messages. Tool-bearing assistant content is NOT automatically published.
Delegated read-only workers do not gain reply_now; they cannot publish through this feature.

No-current autonomous/task contexts can reply without invented IDs. Invalid attribution
fails closed. Only confirmed same-turn public bodies enter the receipt ledger; an exact
trimmed duplicate final body is suppressed. Distinct or extended final information remains.
A later turn does not inherit dedup state. Empty or SILENT interim bodies do not send.
Failed/unknown delivery is not retried or recorded as success. Permission changes continue
to apply at each actual send; unrelated incoming chat does not cancel the active task.

## Verification and rollout

1372passed,24platform skips,25subtests; all93native routes audited.24new integration cases
exercise actual on_message/provider/native loop/actual _send with simulated event transport:
reply_now -> tool -> reply_now -> final yields3messages in one4-request transcript,paired
call IDs,unchanged persona and frozen schemas. Tests cover failure,policy,silence,quote,
nested JSON,exact final dedup and native task/autonomous paths. Static/compile/release checks
clean. An isolated real-provider probe records intermediate delivery locally instead of
posting QQ messages; this explicit test request is not a production forced-action rule.

Deploy committed bytes only,baseline/hash prechecks,backup,exact-plugin native graceful
reload,PID/policy/hash checks,and temporary access revocation. No service restart or live
public QQ test sends; no active drawing task cancellation. Provider transport failure in
historical task was observed but is not claimed solved by adding intermediate replies.
