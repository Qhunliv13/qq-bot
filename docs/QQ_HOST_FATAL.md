# Fixed host fatal private capture

## Result: both streams inspected; initiating SIGTRAP reason remains unknown

Exactly one native experiment completed after the initial pre-launch destination refusal. The private output was inspected directly, not guessed from pattern categories. **Stdout156bytes contains startup/preload success notices, not an assertion or fatal explanation. Stderr555bytes contains missing-system-bus diagnostics plus two cache-write missing-location failures.** These observations do not establish which condition initiated SIGTRAP; cache failures and absent system bus must not be promoted to a proven cause.

The authored JavaScript entry makes no explicit native-binding calls. This is **not** evidence that the actual vendor host runs no native code: stdout reports successful vendor preload activity independently of the authored entry. Those notices establish only what the vendor reported, not verified binding exports, initialized sessions or working native APIs.

All30 samples passed the process-identity guard. Main core-dumping/raw exit status again changed from0/0 in the first sample to1/5 for the remaining29. The30-second watchdog retired the process at30.05s with supervisor return-9. Source identity, synthetic identity and all five pristine software hashes match the existing exit evidence. Login, native binding and send/receive remain unverified; `safe_to_login=false`.

Both complete streams fit below16KiB and were privately preserved. Public evidence contains hashes/lengths and existing fixed metadata only; no raw application text or base64 is published. The original requested private destination remains an empty preserved preflight artifact; the actual capture uses a separately parent-approved Linux-private file and private Windows export. No native retry occurred.

The fixed `host_dispatch_fatal_offline` mode reuses the exact exit-control entry (SHA256 `4ab8582d780a2cbf4a1fdddc90990dc0c74d594edc8f177dfe219b2b61993a94`), synthetic identity, detailed resource/wait/exit sampler, detailed categories, isolated private services, executable arguments, resource limits and watchdog. No native binding, account state, memory or core data is inspected; no sandbox or network setting changes.

## Private boundary

[The fixed wrapper](../tools/qq_host_fatal_control.py) requires explicit `--execute --private-output ABSOLUTE_PATH`. The generic destination is validated outside this public repository by the existing exclusive owner-private diagnostic opener. It is reserved before execution using `O_EXCL | O_NOFOLLOW`, mode0600, and never inherited by the isolated child. Existing public evidence is also exclusive and cannot be overwritten.

Before jail cleanup, each application stream is read once with a16KiB bound. Only the internal control stdout JSON carries `_private_fatal_capture`, whose exact keys are `stdout_base64` and `stderr_base64`. The outer parent strips this field before validation or public report assignment, rejects malformed/noncanonical/oversized encodings, and writes the private JSON through its pinned descriptor. The private document preserves exact base64 plus UTF-8 replacement-decoded text for private inspection. Neither is included in public evidence, console output, documentation or Git. The public report includes only captured lengths and hashes in addition to the existing fixed-category/sampled evidence. Existing detailed capture separately records full-stream lengths and prefix truncation.

The unchanged128KiB helper envelope limit remains enforced. Missing/empty output or capture failure is evidence, never permission to retry automatically. No runtime source changes are permitted after the one actual run; only tests/documentation may subsequently describe it.

## Frozen runtime sources

Pre-execution SHA256:

- [Fixed wrapper](../tools/qq_host_fatal_control.py): `91b34368a54fe22ad8e66818e80a53453b67976e743aa9f17dbbcad6f82c2d8b`.
- [Shared control](../tools/qq_ephemeral_host_entry.py): `4b29eccba94ec9c255fc8cb999c316384a56c06ccb554c0064575c7e786427be`.
- [Shared runtime](../tools/qq_isolated_runtime_probe.py): `fc010b44e1f07a9d44a51f57facbfa6a5d4638df27786024321ce43db55928ed`.

## Validation and execution

Focused fake tests cover both streams, zero/156/16KiB/oversized input, exact read bounds, strict private schema, stripping on malformed input, source identity, unchanged sampler/identity routes, explicit consent, exclusive public artifact creation and envelope bounds. Pre-execution validation:41 focused tests passed;176 related host/identity/sampler/isolation tests passed with1 platform skip. Python compilation, pyflakes and diff whitespace checks passed. Windows roundtrip tests mock only the Linux fd-mode validator; production mode0600 validation is unchanged. WSL system Python lacks pytest, so no dependency installation or environment alteration was performed. After parent source-review GO, one CLI invocation was attempted with the explicitly authorized external Windows-mounted destination. It was refused **before any subprocess/application launch** with fixed category `invalid_private_diagnostic_fd`: the DrvFS destination did not satisfy the existing0600 validator. [The new public evidence](evidence/qq-host-fatal-evidence.json) records `application_invoked=false`, `runtime_executed=false`, `experiment_completed=false` and unchanged five software hashes. The reserved private document was inspected and is empty. That first attempt therefore launched **zero native experiments**. No automatic retry, mode relaxation or overwrite was performed. Existing exit evidence remains unchanged.

After separate explicit parent review/GO, an external fixed wrapper was authorized to reuse the same frozen runner, reserve a fresh Linux-local0600 private artifact, and export at most300KiB to a fresh private Windows document. It uses a separate exclusive [Linux-private capture evidence artifact](evidence/qq-host-fatal-linux-evidence.json); the initial refusal evidence and empty private destination are preserved unchanged. Linux file ownership/mode are verified; Windows POSIX mode is explicitly not claimed. Final combined regression including the actual public-artifact assertions:218 passed,1 platform skip; diff whitespace checks passed. Existing tracked evidence has no changes. No commit is made.
