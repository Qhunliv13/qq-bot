# Fixed host resource observation

This control reuses the exact host-identity source, parser, detailed capture,
synthetic passwd/group records, helper staging and UID65534 sampler. Only
`host_dispatch_resources_offline` passes `detailed_resources=True` internally.
Old modes retain their schemas and exact waiter keyword sets.

## Measurements and limits

- Main PID/start identity, UID65534, capability-zero, NNP and private namespace
  gates are unchanged. No host proc traversal or privileged fallback.
- `/proc/<main>/fd` uses streaming `scandir` and `islice` for 256 entries plus
  one truncation sentinel. Only ASCII numeric names contribute to `count` and
  `maxfd`. No readlink, target paths, fdinfo, memory, stacks, syscall or ptrace.
- `visibility` is readable, denied or unavailable. Denied/unavailable counts and
  maxfd are null, not zero. Truncated counts/maxfd describe only the prefix.
- NOFILE is measured separately by the unchanged numeric limits reader. Descriptor
  count and maxfd are not limit estimates; a high maxfd does not prove exhaustion.
  Even a full untruncated count is a sampled instant, not a causal proof or proof
  that a transient EMFILE never occurred.
- At most 64 task entries per sample, 30 samples. `wchan_visibility` aggregates
  sampled, zero, empty, read-denied, unavailable, nonzero and stat-unavailable
  counts. No symbols, names or TIDs are persisted. Per-thread visibility booleans
  are aggregated immediately; existing thread rows keep state/coarse category.
  Failed wchan reads retain valid stat rows as `unknown`; counts survive failed
  or raced thread stat reads. These are read-attempt counts, not guaranteed
  same-thread attribution across a task-ID race. Zero means returned zero: it
  cannot distinguish masking from no visible wait and is not evidence of futex.
- Main flags use Linux stat field 9 (`tail[6]` where tail starts at field 3).
  Process-stat failures return an unavailable sample without exception text.
- Same 30s QQ / 45s outer deadlines, NOFILE128, CPU30, FSIZE16384 and 128KiB
  outer report cap. Aggregates avoid 30x64 expanded per-thread records.
- No JS/native calls, network relaxation, accounts, login or readiness claims.

## Execution

The only CLI options are `--qq-root` and `--execute`. The latter exclusively
creates [resource evidence](evidence/qq-host-resources-evidence.json) before execution;
an existing artifact prevents another run or overwrite. Actual execution requires
parent review/GO. No evidence is manufactured from tests.

## Authorized one-shot result

The parent-approved WSL Ubuntu-TSC run completed once with 30 identity-verified
samples and watchdog retirement at 30.06s. CLI exit 1 records
`host_dispatch_incomplete`, not failure to collect resources. No repeat was run.
The artifact is 116091 bytes (outer unindented report 59596 bytes), below the
unchanged 131072-byte capture cap. Host software hashes remained unchanged.

| Measurement | Actual result |
| --- | --- |
| FD visibility | Readable in all 30 samples; no truncation |
| Initial FD count / maxfd | 4 / 3 |
| Remaining 29 FD counts / maxfd | 53 / 52 in every sample |
| Measured NOFILE soft / hard | 128 / 128 in every sample |
| Wchan read attempts | 697 (initial 1 thread, then 29 x 24) |
| Nonzero / zero | 687 / 10 |
| Empty / denied / unavailable / stat-unavailable | 0 / 0 / 0 / 0 |
| Coarse wait categories | All `other`; no raw symbols retained |
| CPU delta | 396 ticks at 100 Hz; predominantly waiting classification |
| Capture | stdout 156 bytes, stderr 555 bytes; neither capped |

This rules out near-NOFILE exhaustion **at the sampled instants**: actual counts
were far below the measured limit, rather than an inferred count from maxfd or
NOFILE itself. It does not exclude transient exhaustion between samples or prove
any cause of the incomplete dispatch. EMFILE category was false, not proof that
no resource error ever occurred.

The wchan result is **not globally blind or denied**: 687 reads were nonzero and
nonempty, yet outside the coarse category allowlist. The ten zero reads remain
ambiguous. No kernel wait name, futex cause or masking diagnosis can be inferred
from `other`, and no raw symbols were captured.

`onReady`, zero-delay timeout and immediate were observed; `whenReady` and the
1000ms timeout were not. Native login, backend readiness and cutover remain false.
The same synthetic identity records and native-free source were retained.
