# Prepared-host phase dispatch diagnostic

This is one fixed `prepared_phase_offline` experiment, not an account/session/login test. It reuses the existing prepared-connect entry, host/login factories, parser contracts and shared isolation runner. No native calls were added: dlopen, prepare, initialize and connect still occur once in their original order; no account, QR, session initialization, native teardown or network relaxation is permitted.

## Sequential gates

| Completed checkpoint | Wait measured from | Next irreversible operation |
| --- | --- | --- |
| `phase_baseline_1s` | `runtime` | dlopen |
| `phase_loaded_1s` | `after_binding_load` | host.prepare |
| `phase_prepared_1s` | `after_prepare` | controller.initialize |
| `phase_initialized_1s` | `after_initialize` | controller.connect |
| `tick_1s` | `after_connect` | existing 5s/8s observations |

Each wait requires a monotonic delta >=1000ms before advancing. Existing connect 1s/5s/8s deadlines remain unchanged; successful observation takes at least 12 seconds. Existing 30s inner / 45s outer watchdogs, 16KiB bounds, private namespaces/display, offline network verification, unprivileged execution, zero capabilities and no-new-privileges remain unchanged.

The fixed shared runner deletes inherited `QQ_PREPARED_PHASE_CONTROL` for every mode and sets it to `1` only for this mode. The CLI cannot choose environment, source, stages or native calls. The same entry writes to fixed `/tmp/qq-prepared-phase-progress.json` and emits a separate `QQ_PREPARED_PHASE_<nonce>:` final marker. The strict parser admits only exact bounded stage prefixes; strips only four known extra waits to reuse existing host/login/final-record validation. The original mode retains its original progress schema and timing.

A last `after_prepare` checkpoint without `phase_prepared_1s` localizes loss of dispatch to the preparation boundary and proves initialize/connect were not subsequently invoked. Similarly, a `runtime`-only prefix means even the baseline timer did not complete before dlopen. Checkpoints and timer dispatch do not prove callback readiness, authentication or root cause. No final record remains failed/incomplete even if some waits completed.

## One approved execution only

After parent GO and passing fake-only tests:

```powershell
wsl -d Ubuntu-TSC -u root -- python3 /mnt/c/qhunliv13_web/_public_qqbot/tools/qq_prepared_phase_control.py --qq-root /tmp/qq-native-lab-20261027-pristine/opt/QQ --execute
```

Without `--execute`, software inspection only; no artifact is reserved. Execution exclusively creates [phase evidence](evidence/qq-prepared-phase-evidence.json) before native work, so an existing artifact refuses another invocation. It never overwrites evidence; even an empty artifact left by interruption is not permission to retry. The artifact includes the shared runner's authored-source SHA256, staged-module SHA256 values, five host software SHA256 values before/after, isolation checks and bounded progress. Preserve source after the approved run. No actual-run evidence is claimed until that command has completed and the artifact has been validated.

## Single approved actual result

The parent-approved invocation completed once with `watchdog_30s`, elapsed **30.05s**, native process exit **-9**, and only `runtime` at **0ms** in the valid progress prefix. The baseline 1s timer did not complete; no `before_binding_load` checkpoint was reached. Under this frozen source's sequential gates, this attempt did not proceed to dlopen, host.prepare, controller.initialize or controller.connect. This localizes the observed dispatch failure **before binding load**, rather than blaming prepare/initialize/connect. It does not establish the underlying Electron/OS cause, runtime versions, or native readiness; all readiness flags remain false and the missing final record remains a failed/incomplete observation.

All five host hashes match before/after, all offline-network checks pass, and namespace/privilege gates remain true. The bounded stderr category `file_size_limit` is false in this run (unlike a possible interpretation of earlier observations); `dbus_unavailable` is true but is only a category, not proof of causality. No retry was performed.

Frozen authored entry SHA256: `aead917c13298a0d5ee187524416fd929006790add64f33a4a8db18081676257`. Exact five host hashes and staged hashes are preserved in [immutable evidence](evidence/qq-prepared-phase-evidence.json). Runtime source has not changed after this run.

## Tests

Fake-only tests execute the actual authored JS with existing host/login modules and injected native methods/timers. They cover all five dispatch-stall boundaries, no subsequent native call, full prefixes/deadlines, malformed evidence, old-mode compatibility, fixed-source reuse, inherited flag removal, and exclusive artifact reservation. Existing prepared/connect and shared-runner regression tests remain authoritative.
