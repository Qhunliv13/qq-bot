# Exact vendor bootstrap control

## Scope and safety

The fixed `vendor_bootstrap` mode reuses `qq_ephemeral_host_entry.py` and all existing isolation gates. No default wrapper/init/connect/ready modes are changed. It launches only `/opt/QQ/qq` without flags, fuses, sandbox changes, or `ELECTRON_RUN_AS_NODE`. Fresh private home, X authority/display, D-Bus and fonts remain shared-runner responsibilities; no account/session state is copied. The existing offline verifier runs before QQ starts and proves only DOWN loopback and no forwarding routes. QQ is unprivileged, NNP, capability-zero, in private network/mount/PID/IPC namespaces and detached pivot-root. Original software remains read-only.

Before pivot/overlay, the control reads the original outer launcher and requires exactly these 56 ASCII bytes, with no newline:

```javascript
require('../major.node').load('internal_index', module);
```

The fixed authored observer is mounted read-only onto that same existing `/opt/QQ/resources/app/app_launcher/index.js`; only the private manifest `main` changes to `./app_launcher/index.js`. The expression executes at top-level with the same filename and `module` identity, never in another wrapper. The observer uses only fs, Electron app readiness, pure timers, fixed records, and process termination. It makes no native wrapper/engine/login/config/connect/read-account/session calls. Native behavior inside Tencent's own bootstrap is not replaced, intercepted or repeated. In particular, nothing calls `engine.init` after vendor initialization.

Nonce-bound private JSON records are synchronous and strictly schema-validated. The observer writes `before_vendor_bootstrap`, registers a one-shot ready listener and 0ms/5s/8s pure timers, then executes the exact vendor call. It writes `after_vendor_bootstrap` only if that call returns. At 8 seconds it writes a fixed final record and calls `app.exit(0)`, which means observation completed, not login. Fatal handlers write only fixed categories and exit 1, with no exception text, suppression or retry. If vendor work prevents timer completion, the unchanged 30-second QQ / 45-second outer watchdogs retain the last valid progress. No titles, screens, QR, UID, raw logs or private exception details are exported.

## One authorized actual run — 2026-10-29

Environment: default WSL `Ubuntu-TSC`, `/usr/bin/python3`, pristine QQ root `/tmp/qq-native-lab-20261027-pristine/opt/QQ`. Exactly one native execution was performed. See `evidence/qq-vendor-bootstrap-evidence.json`.

- Original vendor expression was read and confirmed byte-for-byte, 56 bytes, before execution.
- Vendor bootstrap **returned**, the zero-delay pure timer fired, and the registered **ready callback fired** with actual `app.isReady() === true`.
- Last checkpoint: `ready_callback`; 5-second and 8-second timers had **not** fired. No final record and no fixed fatal record existed.
- QQ was retired by `watchdog_30s` after 30.05 seconds, exit `-9`; overall status remains **failed / observation incomplete**.
- Actual runtime: Electron 40.0.0, Node 24.11.1, modules 143, N-API 10.
- All offline/isolation gates passed, and all five original host software SHA-256 hashes matched before/after.
- Authored observer SHA-256: `df94643e4b0577a1aa4d0e5e43b1d4821ff2a0e9e2a1928d479115e5625a35f4`.

The run initially emitted conservative top-level `electron_ready_verified=false` / empty versions because the final record was absent. The saved evidence is deterministically reprojected from its strictly validated nonce-bound `vendor_progress`; `raw_report_projection` preserves those original projection fields. This is parser-only processing of the same run, not another execution or fabricated completion. The shared runner now performs the same progress projection before handling an absent final record.

## Conclusion and boundary

The exact vendor-owned bootstrap establishes Electron ready under the unchanged isolated offline environment, whereas the preceding custom-main ready-first control did not reach ready. Therefore waiting for ready *before* Tencent's bootstrap is a causal ordering problem for that custom entry. This experiment does **not** establish which vendor operation later prevented timers, nor prove native wrapper ABI, independent backend readiness, login, account initialization or send/receive. Those flags stay false/unverified. No fallback or subsequent native call/run was performed.

## Reproduction and tests

Execution requires explicit `--execute` and the pinned pristine source. It is not run by tests:

```text
wsl -d Ubuntu-TSC -u root -- /usr/bin/python3 /mnt/c/qhunliv13_web/_public_qqbot/tools/qq_vendor_bootstrap_control.py --qq-root /tmp/qq-native-lab-20261027-pristine/opt/QQ --execute
python -m pytest tests/test_qq_vendor_bootstrap_control.py tests/test_qq_ephemeral_host_entry.py tests/test_qq_native_ready_control.py tests/test_qq_native_connect_control.py -q
```

Targeted regression: **81 passed** across vendor/shared/ready/connect controls. Python AST/compile checks, pyflakes on changed Python/test files, and `node --check` passed. Tests cover exact source validation, fixed source/mode delegation, bounded strict JSON and fatal categories, stage consistency, same-module top-level expression, offline-gate ordering, no sensitive authored API calls, watchdog bounds, truthful ready/incomplete projection, and saved single-run hashes. Parent handoff performs broader repository regression. No production deployment is involved.
