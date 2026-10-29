# Fixed offline host wait-format observation

`tools/qq_host_waits_control.py` selects only `host_dispatch_waits_offline`. It reuses the exact host identity/resources JavaScript source map, synthetic identity, private capture, main-PID/start-time guards, same-UID capability-zero sampler, 30 samples, 64 tasks, 30-second watchdog and 128KiB helper limit. No modules, runtime, limits, networks, accounts or production configuration change.

Only this mode passes `detailed_resources=True, wait_symbols=True`. Old modes keep their schemas and source hashes. Each existing task wchan and the separate actual main `/proc/PID/wchan` is read in binary, at most 128 bytes. A full 128-byte buffer is conservatively invalid because completeness cannot be established. ASCII whitespace is stripped; no other normalization is applied. Before/after main identity validation encloses both observations; failure discards the sample.

Accepted symbols match ASCII `[A-Za-z_][A-Za-z0-9_]{0,95}`. Before symbol matching, `0`, `0x`/`0X` hexadecimal values and hexadecimal-only strings of length at least eight are categorized numeric_zero/numeric_nonzero without retaining digits. Empty, denied, unavailable and invalid_shape are separate fixed categories. No arbitrary text, kernel addresses, thread names or TIDs are retained. No memory, stack, syscall, ptrace, kallsyms or privilege raise is used.

Task symbol counts retain the first eight distinct observed symbols under `symbol:` keys; keys are sorted after sampling. Additional unmatched symbol observations increase overflow_count, not an unbounded collection. Main wait retains a single projected symbol/category. New-mode coarse thread state and wait-category distributions are separate fixed-key counts (`thread_summary`), not paired/per-thread records; this bounds the complete worst-case report under the existing helper cap. All prior modes keep their existing `threads` list.

All-numeric wchan observations mean visibility is opaque: numeric formatting does **not** establish the actual wait cause. A public symbol is a sampled kernel location, not proof of deadlock or root cause. Main and worker observations must be distinguished. The one actual report is reserved using exclusive creation at `docs/qq-host-waits-evidence.json`; a second run cannot overwrite it. Execute only after parent review GO on the nominated pristine WSL2 root.

## Single approved actual result

Executed once in Ubuntu-TSC WSL2 on the nominated pristine root after parent review GO. All 30 samples passed the existing identity guard. The first main observation was numeric_zero/state R; the next 29 main observations were the exact public symbol `do_exit` with state I. In those steady samples the task aggregate retained `do_exit` count 25 each time, plus either one `anon_pipe_write` symbol or one numeric_zero. Thus visibility is **symbolic, not opaque numeric**. The previous coarse `other` classification concealed an actual public `do_exit` location; it was not evidence of an unknown guessed futex/poll wait.

These are sampled kernel locations, not proof of why exit is incomplete or what initiated it. In particular, this report does not establish crash, coredump, pipe ownership, deadlock or root cause. Investigate the exit/pipe-write path next using an independently reviewed observation; do not repeat the same blind probe or modify kernel/freezer state.

QQ reached the unchanged 30-second watchdog; CLI exit 1 reports `host_dispatch_incomplete`, not a failure to collect wait evidence. Main process was retired. Authored JavaScript SHA-256 remains `4ab8582d780a2cbf4a1fdddc90990dc0c74d594edc8f177dfe219b2b61993a94`; nominated software hashes before/after match the prior resources evidence. Login/native-binding verification remains false. Final focused regression: 96 passed; syntax compilation, pyflakes and git diff whitespace checks passed. Worst-case complete serialized report remains below 128KiB without increasing any runner limit.

## Subsequent read-only environment observation

Windows `wsl --list --verbose` identifies Ubuntu-TSC as WSL version 2; Linux
`uname -r` reports `6.18.54.1-microsoft-standard-WSL2`. A separate fixed read-only
query of three public kernel policy files returned:

```json
{"core_pattern":"|/wsl-capture-crash %t %E %p %s","core_pipe_limit":"0","core_uses_pid":"0"}
```

This establishes a configured piped WSL core collector, not that a particular QQ
thread was writing to it. No kernel/sysctl setting was modified, no collector was
stopped, and no dump or process memory was read. A possible crash/collector exit
interaction remains a hypothesis requiring direct bounded evidence; do not infer
the original signal from the eventual watchdog SIGKILL or claim a confirmed cause.
