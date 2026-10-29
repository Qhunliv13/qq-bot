# Fixed offline native connect observation

This opt-in control performs exactly one `controller.connect()` using the reusable
`qq_transport/native_login.js`. It is an ABI/lifecycle observation, not login,
network readiness, authentication, session, send/receive or production-cutover proof.
The strict boolean connect contract remains unchanged pending measured evidence.

## Scope and safety

- Fixed `connect_offline` enum dispatch reuses `tools/qq_ephemeral_host_entry.py`
  and its sole isolation implementation; no alternate runner or caller-supplied JS.
- Fresh private network/mount/PID/IPC namespaces, pivot-root and detached old root.
  An additional read-only gate checks actual sole DOWN loopback, no IPv4 routes,
  and no IPv6 forwarding routes (kernel reject routes on loopback are harmless).
- Existing private X/dbus/font software staging is reused. QQ runs UID/GID 65534,
  no-new-privileges, capabilities zero, with readonly software and private fresh data.
- No production, credential/profile, NapCat, host network route or internet access.
  No QR request, password/session call or guessed QQ-native teardown occurs.
- `listenerLifetime: 'process'` is explicit. A void listener-add return is not proof
  of registration. Only actual known native callback counts can prove delivery.
  Logical dispose is separate from cleanup by actual owner-process retirement.
- Controller `connect()` is called once. A thrown strict return-type rejection is
  caught, and the actual safe controller result-type snapshot is preserved. No
  undefined-return acceptance or configuration change is inferred from a throw.
- The callback window is fixed at 8000 ms. Only seven fixed callback names and
  bounded counts are recorded, never callback arguments, QR or identity/ticket data.
- Output is one nonce-bound, strict-schema record bounded at 16 KiB. Every actual
  record is reparsed through the same shared initialization/snapshot validators.
- All five host software SHA-256 hashes are compared before/after. Exact staged
  main and reusable-module/pristine-metadata hashes accompany the observation.

## Run

Default validation never launches QQ. Only the following explicitly authorized
command launches the fixed observation in the default Ubuntu-TSC WSL distribution:

```text
wsl --exec /usr/bin/python3 /mnt/c/qhunliv13_web/_public_qqbot/tools/qq_native_connect_control.py --qq-root /tmp/qq-native-lab-20261027-pristine/opt/QQ --execute
```

Run non-native tests first:

```text
python -m pytest tests/test_qq_native_connect_control.py tests/test_qq_native_init_control.py tests/test_qq_native_host_control.py -q
```

## Evidence interpretation

The machine-readable `docs/qq-native-connect-evidence.json` is authoritative for the
actual isolated outcome. `native_connect_observation_verified` means only a valid
completed bounded observation and process exit, not that connect was accepted or
that transport is ready. Check `connection.snapshot.connect_result_type`,
`connect_call_returned`, `connection.call_threw`, `native_callback_counts` and
`listener_registration_verified` independently. A measured undefined return with
`call_threw=true` is an expected strict ABI mismatch, not proof native connect threw.

Initial prelaunch attempts refused before `application_invoked`/`runtime_executed`:
first due to a lazy helper import after pivot (fixed by pre-pivot import), then due
to an overstrict route-header assumption. A separate fresh-network inspection
proved WSL exposes an empty IPv4 route file rather than a header-only file. The
fixed gate accepts either empty or header-only and still rejects every IPv4 route.
No native call occurred in these prelaunch attempts; no security gate was removed.

The first actual run (2026-10-29) was retired by the 30-second watchdog, with no
final record. It therefore did not establish whether initialization/connect
returned or whether callbacks arrived. All five host hashes were unchanged and
all isolation/offline gates passed. The parent explicitly authorized a second
actual diagnostic run with synchronous fixed checkpoints before initialization,
before connect, after connect and after the timer. These checkpoints contain only
nonce, stage enum and the same safe controller snapshot; they never read raw
arguments. The second run retains the same strict ABI and isolation. It does not
change Electron readiness handling, call QR or retry connect. The current evidence
records this second diagnostic observation; absence of `after_connect` must not
be interpreted as an undefined/void return.

### Actual second-run result (2026-10-29)

- Last synchronous checkpoint: `after_connect`; actual initialization step
  `initialized`, actual connect call returned, actual return type **boolean**.
- Phase `connecting`, no failure code, all seven native callback counts **zero**.
  Listener-add returned `undefined`; registration callback delivery is unverified.
- The 8000-ms timer was not serviced: no `after_wait`, no final record and no logical
  dispose. The outer application watchdog retired the process after **30.05 s**
  (exit **-9**). This is a failed bounded observation, not a ready transport.
- This separates a returned connect call from a blocked native call. An Electron
  pre-readiness event-loop/timer issue is a possible explanation, not established
  fact. No readiness change or additional native run is included in this task.
- Actual process retirement is verified; native listener removal and logical
  disposal are not. Cleanup relies on the isolated owner process ending.
- All five host software hashes unchanged; all namespace/pivot/UID/NNP/cap-zero,
  private service and offline-network gates passed. Top-level final-record-based
  readiness flags stay false conservatively; the partial checkpoint is the exact
  source for initialization/connect milestone observations.
