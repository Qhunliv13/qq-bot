# Native-free host dispatch: fixed synthetic identity contrast

This control compares the [prior host capture](evidence/qq-host-dispatch-capture-evidence.json)
with one exclusively created [identity evidence artifact](evidence/qq-host-identity-evidence.json).
The earlier bus identity success establishes bus behavior only, not Electron readiness.

## Fixed design

[The entry point](../tools/qq_host_identity_control.py) accepts only `--qq-root` and
`--execute`. It reserves its fixed evidence file using exclusive creation before
execution; existing evidence or a symlink refuses the run. There is no arbitrary
identity, source, output, or mode CLI option.

The only experimental change from `host_dispatch_capture_offline` is enabling the
existing synthetic identity helper in fixed mode `host_dispatch_identity_offline`.
The shared runtime gate permits identity only for the exact tuple
`('bus_identity_offline', 'host_dispatch_identity_offline')`, with all existing
pivot-root, private display/services, extended startup, callable observer, non-userns
and non-native-load restrictions intact. All old host modes retain their original
child-probe keyword arguments; they do not receive a new false identity keyword.

The unchanged helper creates only root-owned, mode-0444 synthetic records in a fresh
private jail, never reading host identity:

```text
/etc/passwd: qqprobe:x:65534:65534:isolated runtime:/tmp/home:/usr/sbin/nologin
/etc/group:  qqprobe:x:65534:
```

Both modes use the exact same [native-free JavaScript](../tools/qq_host_dispatch_entry.js),
SHA-256 `4ab8582d780a2cbf4a1fdddc90990dc0c74d594edc8f177dfe219b2b61993a94`,
source map with no auxiliaries, strict progress parser, count-only external waiter,
and detailed bounded stderr category capture. The same four helpers are copied and
preimported before pivot. No bus client, extra bootstrap, window-role query, wrapper,
login, QR/account access, or native calls are introduced. QQ remains `/opt/QQ/qq`
with unchanged 30-second inner/45-second outer bounds, 16 KiB file cap, UID 65534,
NNP and zero capabilities. Host software hashes are compared before/after and against
the baseline.

## Execution and interpretation

Exactly one actual invocation is permitted after parent review and focused tests:

```powershell
wsl -d Ubuntu-TSC -u root -- python3 /mnt/c/qhunliv13_web/_public_qqbot/tools/qq_host_identity_control.py --qq-root /tmp/qq-native-lab-20261027-pristine/opt/QQ --execute
```

The one parent-reviewed invocation completed with CLI exit 1 because the host
observation remained incomplete (not a setup failure). No second run was performed.

| Observation | Prior capture | Synthetic identity |
| --- | --- | --- |
| Status / completion | failed / watchdog_30s | failed / watchdog_30s |
| QQ elapsed / retirement exit | 30.15 s / -9 | 30.05 s / -9 |
| Immediate and zero-delay timer | true, 332 ms | true, 335 ms |
| `onReady`, `whenReady`, one-second timer | all false | all false |
| stderr / stdout bytes | 1129 / 156 | 555 / 156 |
| Unknown-scope D-Bus category | true | false |
| Missing system bus socket category | true | true |
| Private session bus connect failure category | false | false |

The authored source hash and all five host-software hashes match exactly across
baseline/new observations and before/after the new run. Both synthetic records were
created root-owned, mode 0444; host identity was not read. There were 30 valid
count-only samples, classified `predominantly_waiting_observed` (341 CPU ticks at
100 ticks/second); no window roles were queried. Capture was not truncated and no
file cap was reached. All resource bounds, isolation and privilege restrictions
remained intact.

**Conclusion:** adding the fixed synthetic identity records did not restore native-free
host readiness or the one-second timer in this run. The reduced stderr and removed
unknown-scope D-Bus category do not prove the remaining stall's cause, nor do the
missing-system-bus category or a false error category prove causality or absence of
errors. The separate bus-only success must not be promoted to a host-readiness result.
Native binding, login, session/send-receive, backend readiness, and production
authorization remain false/unverified.

[Regression tests](../tests/test_qq_host_identity_control.py) cover exact source hash,
old-mode call compatibility, strict gate rejection, no arbitrary identity CLI,
exclusive evidence, unchanged helpers/parser/waiter/capture, and actual fair contrast
against the reviewed evidence. The focused suite after capture passed **109 tests,
2 skipped** (Linux-only checks on Windows); Python compilation and `git diff --check`
passed. Parent runs the full suite after implementation freeze. No commit or
production deployment is part of this control.
