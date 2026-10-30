# No-QQ private bus admission control

[The fixed tool](../tools/qq_bus_admission_control.py) delegates to the existing
[ephemeral runner](../tools/qq_ephemeral_host_entry.py), which invokes the sole
[isolation implementation](../tools/qq_isolated_runtime_probe.py). Its fixed
`bus_admission_offline` branch returns before QQ manifest overlays, entry source
loading, or QQ launch. The pristine software mount and authenticated private X
and bus services are still prepared by the shared runtime. No QQ process starts.

The [existing privilege sampler](../tools/qq_application_observation.py) forks
one trusted callback using the unchanged UID/GID65534, effective-capabilities-zero,
NNP privilege hook. The callback independently snapshots its own `/proc` identity
before any client operation. The existing offline-network gate is also reused.
No account files, host identity files, network policy, EXTERNAL policy, machine ID,
resource limits, or production services are changed. No packages are installed;
`/usr/bin/dbus-send` must already exist and be executable inside the jail.

## Three independent connections, not a stage-coupled proof

1. One raw AF_UNIX connection to `/tmp/runtime/bus` sends exactly NUL followed by
   `AUTH EXTERNAL 3635353334\r\n`. It reads at most 256 bytes under one second,
   accepting only `OK ` plus 32 hex digits and CRLF. It closes without `BEGIN`.
   Only a fixed category survives; the GUID and response bytes are discarded.
2. A separate `dbus-send --peer=unix:path=/tmp/runtime/bus` invocation explicitly
   calls `org.freedesktop.DBus.Hello` with a one-second reply/outer timeout.
   `--peer` avoids an implicit Hello before this explicit call.
3. Another `dbus-send --bus=unix:path=/tmp/runtime/bus` invocation calls
   `org.freedesktop.DBus.GetId`. Its public client establishes its own bus session
   including Hello. It does not send an extra explicit Hello.

Each public-client command records only its exit code, return-zero boolean and
fixed outcome category. Both stdout and stderr go directly to `/dev/null`; no
reply body, GUID, unique name or raw error is captured. Return zero means the
public client returned success, not that this tool parsed a reply signature/body.
AUTH failure does not suppress the independent method controls.

The trusted callback has a four-second parent watchdog and a 16 KiB result cap.
The unchanged namespace runner has a 45-second outer watchdog and process-group
cleanup. Namespace PID1 retirement kills any surviving descendants. An incomplete
callback fails closed; completed negative observations remain diagnostic evidence,
not a passing bus-admission or QQ-readiness assertion.

## Invocation and evidence

On Linux as root, with an explicitly nominated pristine software root:

```text
python3 tools/qq_bus_admission_control.py --qq-root /explicit/pristine/QQ --execute
```

Without `--execute`, only the existing validation path runs. Execution exclusively
creates `docs/evidence/qq-bus-admission-evidence.json` and refuses to overwrite existing
evidence. The existing runner records five software hashes before/after execution.
No command, bus address, callback, identity data or QQ flags can be supplied by CLI.

This control tests the hypothesis in [the diagnosis](QQ_PRIVATE_BUS_DIAGNOSIS.md).
It cannot establish a single connection's AUTH→Hello→GetId progression, a missing
identity file as the root cause, or Electron/QQ readiness. Further controls require
separate review; do not relax the jail to force a positive result.

## Actual baseline (2026-10-30, one approved execution)

The [exclusive evidence](evidence/qq-bus-admission-evidence.json) records:

- Numeric EXTERNAL AUTH: `auth_ok` (the fixed valid OK response was observed).
- Independent explicit Hello: public client exit **1**, `returned_zero=false`.
- Independent GetId session: public client exit **1**, `returned_zero=false`.
- Callback completed in **0.009 seconds**; UID/GID fields were all **65534**,
  effective capabilities were zero, and NNP was set.
- Private display and bus setup succeeded, loopback was down with no forwarding
  routes, and all five host software hashes were unchanged.
- `application_invoked=false`, `runtime_executed=false`, and invocation argv was
  empty. No QQ application process was launched.

`status=observed` means the bounded experiment completed, **not** that bus method
admission succeeded. The separate method clients did not report success. Their raw
errors were intentionally discarded, so these exit statuses alone do not identify
an error class or prove post-AUTH failure on the first socket. Missing passwd/NSS
identity remains a hypothesis, not a diagnosed cause. No repeat run was performed.

## Local verification

Mock-only tests cover accepted/rejected/closed/oversized/timed-out/error AUTH,
public-client success/nonzero/timeout/launch error, identity/tool fail-closed gates,
independent connections, callback restoration, and the early no-QQ runner branch.
Focused new/control/application regression suite, including the frozen actual-evidence
regression: **57 passed**. Syntax compilation,
pyflakes and `git diff --check` passed. These tests do not themselves constitute a
real private-bus handshake; the separately generated evidence supplies that result.
