# Native-free host dispatch: bounded error capture

## Design and boundaries

This is the smallest next observation specified by
[diagnostic limits](QQ_HOST_DIAGNOSTIC_LIMITS.md), not another alternate probe.
The fixed [capture control](../tools/qq_host_dispatch_capture_control.py) selects
`host_dispatch_capture_offline` and exclusively creates
[its evidence](evidence/qq-host-dispatch-capture-evidence.json) before executing. Existing
artifacts cannot be overwritten. CLI accepts only `--qq-root` and `--execute`.

The [shared host runner](../tools/qq_ephemeral_host_entry.py) reuses the exact
[dispatch JavaScript](../tools/qq_host_dispatch_entry.js), SHA256
`4ab8582d780a2cbf4a1fdddc90990dc0c74d594edc8f177dfe219b2b61993a94`, the existing strict
progress parser, and the existing external **count-only** sampler. No role queries,
new application/native calls, alternate probes, sandbox relaxation, production
access or account sessions are added. Namespaces, privileges, watchdogs and limits
remain unchanged: 30-second QQ / 45-second outer bounds, file size 16384 bytes,
128 open files, and 30 CPU seconds.

[Shared capture](../tools/qq_vendor_stall_observation.py) adds optional
`detailed=False`; all old modes and default output are unchanged. Only the new mode
uses `detailed=True`. It reads at most 16385 bytes from the already-open private
stderr capture, categorizes only the first 16384 bytes, and retains no raw text.
It never reads stdout. Actual stdout/stderr sizes come from `fstat` on those same
open handles. Their independent `*_cap_reached` flags mean **size >= 16384**;
`stderr_prefix_truncated` means **read length > 16384**, strictly. A cap-saturated
16384-byte file can therefore have truncation false. No stdout-prefix claim is made.

## Fixed detailed categories

Matching is case-insensitive, token bounded, and line-local. Path matches reject
prefix/suffix path lookalikes. These are observed message categories, not root causes.

- `system_bus_socket_missing`: exact `/run/dbus/system_bus_socket` and `ENOENT` or
  `No such file or directory` on the same bounded line.
- `private_session_bus_connect_failure`: exact `/tmp/runtime/bus` and `failed to
  connect`, `connection refused`, `connection failed`, `could not connect`, `unable
  to connect`, `ENOENT`, or `No such file or directory` on the same bounded line.
- `dbus_scope_unknown`: `dbus` or `d-bus` plus `failed`, `failure`, `error`,
  `unavailable`, `refused`, `could not`, or `unable to`, on a line not matched by
  either scoped category. A successful DBus message does not match.
- `resource_emfile`: `EMFILE` or `too many open files`.
- `resource_efbig`: `EFBIG`, `file too large`, or `file size limit exceeded`.
- `resource_enospc`: `ENOSPC` or `no space left on device`.
- `profile_create_or_open_failed`: `failed to create profile`, `failed to open
  profile`, `cannot create profile`, or `cannot open profile`.
- `sandbox_fatal`: `no usable sandbox`, `sandbox initialization failed`, or exact
  `FATAL` and `sandbox` tokens on the same line. `nonfatal sandbox enabled` is false.

Legacy categories remain available unchanged and may be broader substring matches;
for example legacy `sandbox` is not the new `sandbox_fatal` finding. No counts,
paths, arguments, excerpts, titles or arbitrary environment values are returned
from stderr. False means **not observed in this bounded prefix**, never no error;
`category_false_means_no_error` and `root_cause_proven` are explicitly false.

## Validation

Focused fake-only tests: **104 passed** (capture, dispatch, roles, stall and shared
integration). Python compilation and `git diff --check` passed. Tests cover exact
source hash, parser/sampler routing, fixed helper staging, no role dependency,
exclusive artifact refusal before execution, argument injection, scoped same-line
matching, substring/path false positives, secret non-retention, no stdout read,
prefix bounds, cap equality and default-output compatibility.

## Native observation

Exactly one execution followed the focused pass and explicit parent GO:

```powershell
wsl -d Ubuntu-TSC -u root -- python3 /mnt/c/qhunliv13_web/_public_qqbot/tools/qq_host_dispatch_capture_control.py --qq-root /tmp/qq-native-lab-20261027-pristine/opt/QQ --execute
```

The [exclusive evidence](evidence/qq-host-dispatch-capture-evidence.json) distinguishes:

| Observation | Result |
| --- | --- |
| `system_bus_socket_missing` | **true** |
| `dbus_scope_unknown` | **true** |
| `private_session_bus_connect_failure` | false — not observed |
| `resource_emfile`, `resource_efbig`, `resource_enospc` | false — not observed |
| `profile_create_or_open_failed`, `sandbox_fatal` | false — not observed |
| Actual stdout / stderr size | **156 / 1129 bytes** |
| stdout / stderr cap reached | false / false |
| stderr prefix truncated | false |

Legacy `dbus_unavailable` is also true; all other legacy categories are false.
This establishes emitted missing-system-bus-socket diagnostics and a separate
unscoped D-Bus failure diagnostic. It does **not** establish that either caused the
dispatch stall, nor that the private bus client protocol works. Unlike the previous
empty capture, this is positive scoped error evidence. Captured stdout/stderr did
not saturate the file cap; descendant profile/cache writes remain subject to that
same cap and were not measured by this observation.

The watchdog retired the process at 30.15 seconds (`exit_code=-9`). CLI exit 1
corresponds to the correctly retained `host_dispatch_incomplete` failure, not a
successful-ready claim. `setImmediate` and `setTimeout0` fired at 332 ms;
`setTimeout1000`, `whenReady` and `onReady` did not appear. All 30 external samples
verified identity, never queried roles, and retained the original resource limits.
CPU delta was 368 ticks at 100 ticks/second; summary remained
`predominantly_waiting_observed`. Wait category `other` remains opaque.

All five nominated software hashes were identical before/after; the authored
JavaScript hash matched the frozen value. Offline-network checks and all required
isolation flags passed. No native binding, login, session or send/receive success
is claimed. No second run or follow-up probe was performed. Any subsequent private
bus protocol or bounded resource-occupancy diagnostic requires separate review;
adding host services or relaxing bounds is not justified by these results.
