# Private QQ native host control

## Scope and provenance

This independently authored control uses standard Node inspector and `process.dlopen`
semantics. It contains no NapCat loader/proxy/code and invokes no native exports,
getters, `get`, configuration, connection, login or session API. Only fixed
`process.versions` strings and own descriptors of the fixed `wrapper.node` exports
are permitted. It is **not** an independent QQ backend and is never production.

The companion inspector is `tools/qq_private_inspector.js`; its parent owns its
source/tests. This control imports `tools.qq_application_observation` for exact QQ
PID/X correlation and its existing `runtime` module for **all** jail construction,
private display/services, pivot-root and privilege hooks. No shared files are edited.

## Fixed execution contract

- Explicit `--execute` is required; otherwise validate only.
- Candidate software: pristine extracted `/tmp/qq-native-lab-20261027-pristine/opt/QQ`.
- QQ argv is exactly `/opt/QQ/qq --inspect=127.0.0.1:9229`; no script argument,
  `ELECTRON_RUN_AS_NODE`, sandbox disabling, or packaged-main override is supplied.
- Full existing `child_probe` gate: private mount/network/PID/IPC namespaces,
  pivot-root with detached old root, readonly software, private authenticated X11,
  private activation-disabled DBus, readonly font software and private tmpfs state.
- Before activating loopback, compare actual namespace against the parent's original
  net namespace, require only `lo`, and refuse non-loopback routes. Execute only
  trusted `/usr/sbin/ip link set dev lo up` in that verified namespace. Never create
  external routes/interfaces or expose host display, sockets, profiles or credentials.
- Read fixed inspector source before pivot, exclusively stage it as
  `/tmp/qq-private-inspector.js` with mode 0444, then execute exactly
  `/usr/bin/node /tmp/qq-private-inspector.js` as UID/GID65534, cap-zero, NNP.
- Inspector environment includes `QQ_ISOLATED_NATIVE_INSPECTOR=1`, the actual outer
  `QQ_INSPECTOR_PARENT_NETNS`, and `NODE_NO_WARNINGS=1`. It does not inherit proxies,
  credentials, loaders, production sockets or account state.
- Bounds: QQ 30 seconds, inspector 12 seconds, outer process-group watchdog 45
  seconds. The inspector has a five-second discovery plus five-second WebSocket
  budget; parent coordination explicitly revised the initial eight-second request
  to twelve seconds so its complete two-phase bounded protocol can run.
- The shared PID1 supervisor owns private service cleanup; its exit also destroys
  remaining descendants. Outer timeout kills its complete helper process group.
- Only one `QQ_NATIVE_INSPECTOR:` JSON record at most 16 KiB is accepted. The exact
  envelope, readiness-false invariants, fixed errors, versions, candidate names and
  bounded export descriptor schema are checked again by Python. Raw QQ/inspector
  errors, endpoint identifiers, window titles and export values are never reported.

## Recorded actual execution

On 2026-10-29 (Asia/Shanghai), the root WSL run completed the 30-second QQ window.
See `docs/evidence/qq-native-host-control-evidence.json` for the machine-readable record.
The private display and private DBus became ready. Loopback was activated only in
its verified isolated namespace. QQ executable/window PID correlation was observed.
The inspector returned fixed `discovery_timeout`, exit 1: **no inspector endpoint
was discovered, no runtime versions verified, and no binding load attempted**.
The evidence status is `failed`; native binding/session/login/backend readiness
remain false. No fallback is implemented and absence is never treated as success.
Final staged inspector SHA-256 (confirmed equal to the stable source after run):
`d6ba6bded7907fc5c310caa066b518385ba5a383d5830de1688735a880cea7fa`.

`_NET_WM_PID` is client-supplied; correlation against same-sample `/proc` executable
identity is not cryptographic X client ownership or login readiness. Cap-zero
refers to the verified QQ/inspector launch hook; individual Chromium descendants
may create their own user namespaces/capability sets, which are recorded separately.

## Reproduction and checks

Run from repository root in WSL with already-installed software and the pristine
candidate; no root software installation or modification is part of this task:

```text
sudo /usr/bin/python3 tools/qq_native_host_control.py --qq-root /tmp/qq-native-lab-20261027-pristine/opt/QQ --execute
python -m pytest tests/test_qq_native_host_control.py -q
python -m pyflakes tools/qq_native_host_control.py tests/test_qq_native_host_control.py
python -m py_compile tools/qq_native_host_control.py tests/test_qq_native_host_control.py
```

Tests check pessimistic readiness defaults, exact argv, invalid/oversized output,
readiness escalation, redacted failures, parent namespace equality, unexpected
interfaces/routes, RUN_AS_NODE rejection, full reused gate, and missing-source
refusal before launch. Final local validation: **24 owned tests pass**, **41 tests
pass** when combined with application-observation and private-service suites;
pyflakes and Python compilation are clean. Host-control helper process scan after
the completed run returned no matches (pgrep exit 1 is the no-match result).
A future successful wrapper load would prove only that
binding's loading in that measured host—not export ABI, session initialization,
login safety, transport, send/receive, backend completeness or production readiness.
