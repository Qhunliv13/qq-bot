# Local isolated QQ runtime experiment

## Current measured milestones (2026-10-29)

Later experiments below supersede earlier **unknown** native-host conclusions; older
failure records remain historical evidence, not the latest capability assessment.

- `tools/qq_application_observation.py` genuinely launches the default pristine app.
  Private X window PID properties correlate with the actual QQ executable/process;
  this is not authentication or login readiness. See
  `qq-application-observation-evidence.json`.
- `tools/qq_ephemeral_host_entry.py` genuinely loads `wrapper.node` **inside the QQ
  executable**, with Electron 40.0.0, Node 24.11.1, module ABI 143 and N-API 10.
  Engine, LoginService, WrapperSession and StartupSession candidate exports exist.
  Its only modifications are read-only file overlays inside the detached private
  mount namespace; all five original software hashes match before and after.
  See `qq-ephemeral-host-entry-evidence.json`.
- `tools/qq_native_init_control.py` genuinely obtains engine/login services, initializes
  their explicit same-build configurations and calls listener registration. The actual
  registration return is **undefined**, contradicting the consumer number annotation.
  Explicit process-lifetime policy accepts this without inventing an unregister token,
  asserting callback registration proof or claiming native listener removal. Actual
  process exit retires the owner. See `qq-native-init-evidence.json`.
- Independently authored `qq_transport/native_login.js`, `native_session.js` and
  `native_messages.js` implement owner-driven lifecycles with offline test coverage.
  Native session configuration, real account authentication, session-ready callbacks,
  message send/receive, full feature parity and production cutover are **not verified**.

- `qq-native-connect-evidence.json` records an actual **boolean return** from one
  offline connect request at its synchronous `after_connect` checkpoint. All seven
  observed callback counts are zero; the later eight-second timer did not finish before
  the 30-second watchdog. This does not prove callbacks were registered or explain why
  the timed wait did not finish.
- `qq-native-ready-evidence.json` separately waits for standard Electron readiness
  **before loading the wrapper**. A zero-delay JS timer fires, but `app.isReady()` stays
  false and `app.whenReady()` does not resolve during the measured window. No native
  load/init/connect is reached in that experiment. Therefore, do not label the prior
  timeout a demonstrated native-connect block or ABI error; the custom-main host
  lifecycle still needs evidence-based integration with Tencent's normal bootstrap.

An inspector attempt did not discover an endpoint; it is not the successful loading
path and does not identify why this build did not expose the requested endpoint.
The corrected normal Electron main path uses an existing **relative** launcher file:
Electron joins the app directory with `package.main`. The initial absolute temporary
path was not read, while the corrected relative launcher was read and produced the
nonce-bound real host record. No sandbox/fuse patch or security bypass was involved.

This is not a working QQ transport or proof of personal-account feature parity.
Production NapCat, its persistent volumes, QQ login state and AstrBot remain unchanged.

## Software-only preparation

A pristine Tencent-hosted `linuxqq 3.2.34-53644` DEB was downloaded outside the repository.
Before local WSL extraction its entire SHA-256 was checked against the previously
recorded `436c65e1dd28422e128a21cbddb5fe4e2e6c079d7f27fec2541e014d38cc336e`.
`dpkg-deb --extract` unpacked software into a fresh lab directory. It did **not** install
the package, run postinst/postrm scripts, replace system QQ or copy any account data.
The original main remains `./application.asar/app_launcher/index.js`.

Static preflight of this complete extracted software confirms the archived entry,
wrapper.node and major.node ELF64 x86-64 headers with no metadata errors. Static
success still exits non-ready and does not establish a usable native binding.

## Opt-in version-only probe

`tools/qq_isolated_runtime_probe.py` defaults to validation without executing anything.
Actual execution is Linux-only and needs explicit `--execute`. It attempts a saved
script which reads a strict allowlist of `process.versions` under `ELECTRON_RUN_AS_NODE`.
The saved script does not import QQ main/native modules, call QQ services, perform
login or send messages. The executable can ignore that mode and enter its pristine
application bootstrap before the script; that is why the account-free filesystem and
network boundary are mandatory. Do not assume the fuse or host supports it in advance.

The execution boundary requires:

- Separate network, mount and PID namespaces, verified against parent namespace IDs.
- A fresh chroot with no host account home, production mounts or runtime sockets.
- Read-only software/system-library bind mounts, private temporary directories and proc.
- A newly constructed environment with no inherited tokens, proxy, Node or loader options.
- Dropped supplementary groups, UID/GID 65534, no retained effective capabilities and
  `NO_NEW_PRIVS` before the executable is invoked. No sandbox-disabling QQ flag.
- Bounded duration and output, disabled core dumps, and termination of namespace children.
  The trusted namespace PID1 supervisor retains its credentials and parent-death
  signal; only the QQ subprocess drops UID/GID in its pre-exec hook. This avoids
  credential changes clearing the supervisor's parent-death signal and weakening
  the outer watchdog. The actual version script still runs unprivileged.

Do not weaken any boundary to make a failed test pass. The probe is an experiment
harness, not a security certification for arbitrary hostile software or a production
hosting mechanism. Software origin is a caller responsibility: a path or ELF magic
cannot prove that a directory is pristine or contains no account state.

## Interpretation

Only a successful nonce-marked, allowlisted runtime record establishes the observed
runtime versions. An executable exit, absent output or mocked test result does not.
Even observed Node/Electron versions cannot prove that the QQ application bootstrap,
`qq_magic_napi_register`, JS exports, session lifecycle or any of the 180 action handlers
work independently. Every native-binding, login and production-cutover readiness flag
must remain false. No real account test can be inferred from this version-only probe.

## Actual observed result (2026-10-27)

Two attempts used the same isolation boundary; the second added bounded redacted
stderr classification. Namespace, chroot and privilege checks succeeded. The runtime
exited with **-5 (SIGTRAP on Linux)** and no verified nonce-marked version record.
The failure classification is `unclassified_nonzero_exit`, not a missing-library,
fuse, sandbox or ABI diagnosis. Those causes are not established by a signal number.
No isolation check was bypassed and no account/login test was performed. See
`qq-isolated-runtime-evidence.json`. This experiment has **not** passed its runtime gate.

## Follow-up diagnostic (same protection boundary)

A third, explicit private-diagnostic run writes only a bounded stderr prefix to a
new `0600` file outside the repository. The descriptor is passed to the trusted
supervisor, not QQ; raw stderr is never printed in public JSON or committed.
The observed fatal identifies the **SUID sandbox helper configuration** requirement.
The extracted helper is root-owned mode 0755, matching the archive, while the package's
unexecuted postinst references 4755. No setup script or permission change was applied.

The binary's static RunAsNode fuse is enabled. That does not establish why this build
still enters a Chromium sandbox path with the environment variable set. Changing only
file mode is not a demonstrated solution, since nosuid and NO_NEW_PRIVS intentionally
remain active. Do not disable them, patch fuses or use `--no-sandbox` to manufacture
success. See `qq-runtime-sandbox-diagnostic.json`; native initialization and login
remain unverified.

## Trusted namespace control: a limitation in the experiment harness

The opt-in `--userns-control` mode runs only trusted
`/usr/bin/unshare --user --map-root-user /usr/bin/true` through the same filesystem,
network, PID, UID and capability boundary. It never invokes QQ and keeps all runtime
and native verification flags false. The actual inside-chroot command returned 1
with `unshare failed: Operation not permitted`.

A separate non-chroot control, already inside private network/mount/PID namespaces
and running as UID/GID 65534 with no-new-privileges, returned 0. Thus user namespaces
are available in this WSL environment at least in that context. These arms are not
otherwise perfectly matched; the result is consistent with the documented kernel
EPERM condition for CLONE_NEWUSER when a chroot root differs from the mount namespace
root. It does not prove that all QQ failure causes are explained.

The control distinguishes a harness limitation from failure of independent QQ in
principle. A future, separately reviewed pivot_root-style boundary may replace the
mount namespace root and detach the old root without removing filesystem isolation.
It is **not implemented or validated by this control**. Do not infer that chroot should
simply be removed or that setuid/native/login testing is authorized.
See `qq-userns-control-evidence.json` for the actual command outcomes and limitations.

## Detached-root control and actual runtime follow-up

An explicit `--filesystem-mode pivot-root` now self-binds the fresh jail, replaces the
mount namespace root, verifies the new root's device/inode, detaches the old root and
removes its newly created empty marker before any runtime/control exec. Defaults remain
chroot. Namespace checks, read-only binds, UID/capability/NNP checks and supervisor death
handling are retained. A source-only independent review found no critical issue under
the trusted-software assumptions; this is not a security certification.

The actual trusted user-namespace control **succeeded** under pivot-root, with no QQ
execution. The subsequent real QQ version experiment still **failed by timeout**. Its
private stderr reached GUI initialization and reported missing system DBus and an X
server/DISPLAY; it did not emit a verified runtime version record. No production display,
socket, credentials, account state or network was exposed to repair that failure.
The intended script is Node-only, but the actual QQ build demonstrably reached a GUI
path; do not claim the executable accepted generic Node-only mode. A next GUI/native
experiment needs a separately planned private display, not a borrowed production session.
See `qq-pivot-root-evidence.json` for outcomes, limits and unchanged cutover requirements.

## System-Node native-load control (2026-10-28)

The opt-in `--native-load-control` runs a fixed script with `/usr/bin/node` through
exactly the existing namespace, read-only software, detached pivot-root, UID 65534,
cap-zero and no-new-privileges boundary. It is mutually exclusive with userns control.
No arbitrary script/command is accepted. It imports only pristine `wrapper.node` and
`major.node`, lists bounded own-property descriptors without invoking getters or
exported functions, and accepts one strictly validated nonce-marked JSON record.
It never loads QQ main, copies account data, logs in, or calls a native service.

Reproduce in the existing account-free Linux lab:

```bash
python tools/qq_isolated_runtime_probe.py --qq-root /path/to/pristine/opt/QQ --filesystem-mode pivot-root --execute --native-load-control
```

Actual observed system Node **18.19.1**, module ABI **109**, N-API **9**: `major.node`
loaded and exposed `load` as a function (not called); `wrapper.node` failed with
`ERR_DLOPEN_FAILED` and an undefined-symbol classification. The exact symbol is not
published or inferred; missing-library, module-version-mismatch and exact
`qq_magic_napi_register` match flags were false. Static inventory separately shows
that wrapper **imports**, rather than exports, `qq_magic_napi_register`.

See `qq-native-load-control-evidence.json`. This proves a limited real native module
load in **system Node**, not Tencent Electron compatibility, QQ runtime bootstrap,
session initialization, independent actions/events or replacement readiness. Thus
`runtime_executed`, `runtime_versions_verified`, `native_binding_verified`,
`safe_to_login` and all backend/cutover readiness flags remain **false**. The native
control can succeed as an experiment even while a candidate load fails.
Existing Xvfb was discovered locally; no display server was started or package installed
by this control, and the earlier GUI limitation is not resolved by this Node result.

If this step succeeds, the next separate gate is an independently authored, narrowly
scoped native-binding experiment in the same account-free and network-isolated lab.
If it fails, record the exact redacted failure and investigate prerequisites without
using production credentials, NapCat runtime APIs or a renamed loader as a fallback.

## Private authenticated display experiment (2026-10-28)

`--private-display` is an explicit runtime-only option requiring `--filesystem-mode
pivot-root`. It is refused for chroot, userns control and native-load control. It does
not grant execution consent. The new display starts only after root replacement and
old-root detach; no system package installation, host display, account directory,
production service or NapCat bootstrap is used.

The fixed `/usr/bin/Xvfb :99 -screen 0 1024x768x24 -nolisten tcp -auth ...` runs through
the same UID/GID 65534, no-new-privileges, cap-zero and PID1-parent checks as QQ.
A fresh private `0700` X socket directory belongs to UID 65534; readiness checks its
actual socket type and owner. A new `0600` Xauthority file holds a random MIT cookie
inside the jail. There is no `-ac`, inherited DISPLAY/XAUTHORITY or externally exposed
TCP listener. A new IPC namespace and a fresh bounded tmpfs `/dev/shm` avoid host IPC
and fix the first observed Chromium shared-memory fatal without any sandbox bypass.
Xvfb stderr is captured separately under the existing file-size limit; only fixed
booleans are returned. QQ private diagnostics remain an optional new `0600` outside-
repository file; no raw diagnostics or identifiers enter committed evidence.

Reproduce with a fresh private diagnostic filename on each explicit run:

```bash
python tools/qq_isolated_runtime_probe.py --qq-root /path/to/pristine/opt/QQ --execute --filesystem-mode pivot-root --private-display --private-diagnostics /tmp/new-private-stderr
```

**Actual result:** authenticated Xvfb was ready, socket owner verified, privilege and
namespace checks passed, and QQ was actually executed with the version script and
`ELECTRON_RUN_AS_NODE=1`. The new private `/dev/shm` removed the previously observed
shared-memory fatal. QQ still timed out after 10 seconds, with **no nonce-marked
runtime record**. Fixed diagnostic categories were DBus unavailable, fontconfig
unavailable, and crash-reporter activity; shared-memory unavailable was false. No DBus
server was added: these messages do not prove DBus is the remaining root cause.
The launcher source entry remains the pristine package's
`./application.asar/app_launcher/index.js`; it was only read, not patched or invoked
with invented native arguments. GUI/bootstrap behavior despite RUN_AS_NODE is observed,
not successful Node-only execution or proof that an Electron fuse is disabled.

See `qq-private-display-evidence.json`. Runtime versions, native binding, session/login,
independent-backend and cutover readiness remain **false**. This is real progress past
the missing-display/shared-memory gates, not completion of the independent transport.
The focused mock regressions (isolated runtime, native-load probe, private display)
returned **83 passed, 1 Windows-specific skipped**; tests alone are not runtime evidence.

## Offline pristine bootstrap entry gate (2026-10-28)

The new opt-in `--bootstrap-readiness` performs **static-only** inspection of the fixed
Tencent `QQ 3.2.34-53644` package entry. It rejects `--execute`, display, native and
userns controls before reading any software, never spawns QQ/Node/Electron, and returns
exit status 1 even when static metadata is observed: success is **not readiness**.
The only software reads are the QQ executable's four-byte ELF check, exact package
metadata, ASAR header and its at-most-4096-byte `app_launcher/index.js` payload.
It reuses the existing strict path and ASAR metadata parsers; no guessed loader,
decryptor, native export call or source rewrite is involved. Caller-verified package
provenance is still required; this metadata check cannot attest pristine origin.
The runtime helper's copied-file execution stays independent of these offline imports.

Run against the existing software-only lab (no isolation needed because nothing executes):

```bash
python tools/qq_isolated_runtime_probe.py --qq-root /tmp/qq-native-lab-20261027-pristine/opt/QQ --bootstrap-readiness
```

**Actual WSL observation:** package `main` is
`./application.asar/app_launcher/index.js`; the packed entry is 80 bytes (ASAR offset
1200, header payload start 386532), SHA-256
`0797da28c00619c2110cd622447d43cb9bc8e09fa47751105acd562ecaee9264`.
It fails strict UTF-8 decoding; thus it is **not plain UTF-8 JavaScript source** that
can simply be fed to system Node. The separate outer `app_launcher/index.js` is a
56-byte call to `require('../major.node').load('internal_index', module)`; the prior
Node control observed only a `load` function descriptor, not its invocation or a
supported API contract. Electron's documented ASAR support alone does not establish
that it can decode this package-specific payload or finish QQ initialization.

The previous actual QQ run under detached pivot-root, private network, cap-zero UID,
private Xvfb and bounded 10-second watchdog timed out without a nonce record; DBus/
fontconfig diagnostics are observations, not proven causes. The offline step cannot
resolve that gate. See `qq-bootstrap-readiness-evidence.json` and the earlier two
probe evidence files. No login/session import, NapCat proxy, production mutation or
readiness claim follows; further native/bootstrap execution needs a separate safe
experiment retaining **all** existing boundaries and an independently validated
interface, rather than invoking `major.node.load` with guessed parameters.

## Extended private-display bootstrap observation (2026-10-28)

The only safe next runtime observation is an explicit `--extended-startup` flag on
the existing private-display/pivot-root execution gate. The default remains the
10-second watchdog. This flag never executes without explicit `--execute` and is
refused unless private authenticated Xvfb and pivot-root are selected; it cannot combine with native-load or userns
controls. The same clean environment, isolated network/mount/PID/IPC namespaces,
read-only software binds, cap-zero UID/GID 65534, no-new-privileges, private
`/dev/shm`, bounded outputs and unchanged QQ invocation remain in force. QQ is
never given an arbitrary startup argument or a sandbox-disabling flag. The
opt-in child timeout and CPU limit are 30 seconds; the outer helper watchdog is
35 seconds, so helper/setup hang remains distinguishable from a QQ child timeout.

The redacted diagnostic phases are narrowly **boundary verified** (namespace
and detached root in `isolation`), **private display ready** (`private_display`),
**QQ invoked** (`runtime_launch_attempted`/`runtime_executed`), and **30-second
watchdog** (`startup_observation=qq_invoked_watchdog_at_30s` only if the QQ child
timed out). These are harness observations, not claims that QQ reached a
particular internal initialization phase. No milestone is inferred from DBus,
fontconfig or crashpad stderr categories. A failed helper setup or outer timeout
does not receive a QQ-watchdog marker. No raw logs or account identifiers are
published. Reproduce only in the existing software-only, network-isolated lab:

```bash
python tools/qq_isolated_runtime_probe.py --qq-root /tmp/qq-native-lab-20261027-pristine/opt/QQ --execute --filesystem-mode pivot-root --private-display --extended-startup
```

**Observed:** all isolation and authenticated private-display checks passed, QQ
was invoked, and the **30-second watchdog expired**. `runtime_record_present=false`;
no nonce-marked Electron/Node record was produced. Fixed stderr categories
remain DBus unavailable, fontconfig unavailable and crash-reporter activity;
shared-memory unavailable is false. This additional bounded time did **not**
resolve the runtime gate or identify a blocker inside Tencent's native code.
Runtime versions, native binding, login and independent backend remain
**unverified/not ready**. The ASAR inventory lists `app_launcher/index.js`,
`app_launcher/launcher.js` and `app_launcher/adm-zip.js`, not an `_bootstrap`
entry. `major.node.load('internal_index', module)` occurs only in the outer
launcher; its ABI cannot be established from the export descriptor or from
this timed-out run. Do not call it with guessed parameters, decode the 80-byte
packed entry by speculation, or use live credentials. Next progress requires
a validated vendor interface or further safe evidence, not a fabricated backend.

Mock-only focused tests: 59 passed, 1 Windows-specific skipped. They verify
gating and report propagation but cannot verify Tencent startup.

## Private runtime services and independent GUI surface (2026-10-29)

The additional **explicit** `--private-runtime-services` flag requires the private
authenticated display and detached pivot root; `--execute` remains separately required.
It is refused for controls, chroot and offline readiness. Only after the namespace
checks and root detach, the jail binds `/etc/fonts` read-only (not all of `/etc`) and
sets private fontconfig paths. A fresh per-run configuration starts the trusted system
`/usr/bin/dbus-daemon --nofork --nopidfile` as UID 65534 under NNP and cap-zero, with
`EXTERNAL` authentication and one socket in private `/tmp/runtime`. There are no
service directories, activation helpers, host session sockets, inherited credentials
or host machine-id mounts. The daemon and Xvfb remain within private network, mount,
PID and IPC namespaces. Its socket must be an actual Unix socket owned by UID 65534.
A fixed system `xwininfo` periodically counts X **root children only**: no window
title, process command line, address, stderr or raw X output enters public JSON.
A child count is not proof of who created windows. Both new helpers share the existing
watchdog, no-new-privileges and cap-zero checks; no QQ flags, fuses, native exports or
account paths are modified. Run only against the already verified software-only lab:

```bash
python tools/qq_isolated_runtime_probe.py --qq-root /tmp/qq-native-lab-20261027-pristine/opt/QQ --execute --filesystem-mode pivot-root --private-display --private-runtime-services --extended-startup
```

**One actual isolated run:** private Xvfb and the private bus both had owner-verified
sockets; the fontconfig-unavailable diagnostic changed to false. QQ was invoked and
the 30-second watchdog expired. The DBus-unavailable category stayed true, without
identifying which bus emitted the message; the private bus socket's readiness does not
prove QQ used it. `xwininfo` sampled up to three root children, but its initial
baseline was unavailable, so these cannot be attributed to QQ or asserted to be a QQ
window. The intended `/probe.js` still emitted no nonce record. The packaged QQ
launcher may ignore this Node-only script; absence of nonce **does not prove QQ failed
to reach its own native/GUI bootstrap**. No verified QQ-owned window, Electron version,
native session, login, independent transport or production cutover results.
Evidence: `qq-private-runtime-services-evidence.json`. Readiness flags remain false.
Focused regressions on Windows: 72 passed, 1 skipped; static pyflakes passed. Tests
are mock-only, not proof of actual QQ capability.
