# Fixed host exit evidence

## Result: fatal SIGTRAP/core-dump path preceded the watchdog

One authorized execution of [the fixed exit control](../tools/qq_host_exit_control.py), after tests and parent source-review GO, produced [the exclusive evidence artifact](qq-host-exit-evidence.json). Runtime sources were frozen before execution; only tests and this document changed afterward. No commit was made.

| Observation | First sample | Remaining 29 samples |
|---|---:|---:|
| Elapsed seconds | 0.172 | 1.011–29.011 |
| Main `CoreDumping` | 0 | 1 |
| Main stat field52 raw `exit_code` | 0 | 5 |
| Main wait | numeric zero | `do_exit` |
| Sampled threads | 1 | 25 |
| Thread raw exit-code histogram | `{0: 1}` | `{0: 25}` |

All 30 samples passed the before/after process-identity guard. Main `CoreDumping=1` establishes that core dumping was in progress **before** the 30-second watchdog. The contemporaneous nonzero raw status5 has low-seven signal bits5 (`SIGTRAP` on Linux x86-64), not SIGSYS. This distinguishes an already-triggered fatal trap/core-dump path from an ordinary application-service wait. The eventual supervisor return code `-9` at30.05s is watchdog cleanup, not evidence that SIGKILL initiated the failure.

The initiating instruction, sender, software defect, assertion, and why the trap occurred are **not established**. Raw5 lacks the0x80 completion bit; that does not contradict an in-progress core dump or prove a completed core file. The existing generic `root_cause_proven=false` remains appropriate. An intermittent one-thread `anon_pipe_write` is consistent with the dump path but does not identify its pipe consumer or prove a specific core handler blockage. No pipe targets or core contents were inspected.

Thread statuses remaining zero do not disprove the main fatal status: process-wide and per-thread exit bookkeeping differ, and field52 is access-controlled. A denied ptrace-read permission check can make the kernel display zero. The sampler performs **no ptrace operation**, and zeros alone are never interpreted as successful normal exits.

## Verified semantics and bounded projection

Before coding, [Linux proc_pid_stat(5)](https://man7.org/linux/man-pages/man5/proc_pid_stat.5.html) confirmed field52 is the exit status in waitpid form and is marked `[PT]`. The post-comm tail starts at field3, therefore field52 is tail index49 (not the field38 parent-notification signal). [Linux proc_pid_status(5)](https://man7.org/linux/man-pages/man5/proc_pid_status.5.html) documents `CoreDumping` as whether a core dump is in progress. Signal numbering is documented in [signal(7)](https://man7.org/linux/man-pages/man7/signal.7.html).

The sole new fixed mode is `host_dispatch_exit_offline`. It routes through the same source, synthetic identity, capture, private services, UID/capability guard, resource limits and watchdog as [the waits control](../tools/qq_host_waits_control.py). `exit_status` defaults false, requires strict booleans and both `wait_symbols=True` and `detailed_resources=True`; older schemas remain unchanged.

- Main status: one read of at most16KiB, inside the existing identity guard; a full buffer is rejected. Only a unique literal `CoreDumping: 0` or `CoreDumping: 1` survives. Missing, invalid, duplicated, denied or oversized data is null, never false.
- Main/thread stat: only decimal raw exit status0..65535 is added; absent or malformed field52 becomes null. Existing bounded stat reads remain unchanged.
- Thread histogram: the existing64-thread loop only; first eight distinct numeric-string keys plus overflow and unavailable counts. No new scan, TIDs, status text, paths, signal masks or extra context.
- No native JS change, memory/core read, stack/syscall probe, ptrace, sysctl change, sandbox relaxation, or resource-limit change.

## Execution and validation

Executed once, as root in Ubuntu-TSC:

```text
wsl -d Ubuntu-TSC -u root -- python3 /mnt/c/qhunliv13_web/_public_qqbot/tools/qq_host_exit_control.py --qq-root /tmp/qq-native-lab-20261027-pristine/opt/QQ --execute
```

CLI exit1 corresponds to the existing `host_dispatch_incomplete` result, not a failed measurement:30 identity-verified observations were saved. Authored JS SHA256 and synthetic identity match the prior waits artifact; all five pristine software hashes match before/after and the baseline. Native binding, login, backend readiness and send/receive remain unverified; `safe_to_login=false`.

Validation before execution:80 focused tests passed,126 additional sampler/integration/isolation tests passed with1 platform skip; py_compile and pyflakes passed. Post-run frozen-artifact assertions check actual0→1 CoreDumping,0→5 main status, identity/source equality, histogram bounds and report size. Worst-case30-sample report remains below the unchanged128KiB helper cap, and the actual pretty-printed artifact also fits that cap. Exclusive artifact creation prevents an accidental second run overwriting evidence. Final combined regression after adding the frozen-artifact assertion:207 passed,1 platform skip; diff whitespace checks passed.
