# Local isolated QQ runtime experiment

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
It does not load QQ's application main, import a native module, call a QQ service,
perform login, or send a message. The runtime can reject that mode; do not assume the
fuse or host supports it in advance.

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

If this step succeeds, the next separate gate is an independently authored, narrowly
scoped native-binding experiment in the same account-free and network-isolated lab.
If it fails, record the exact redacted failure and investigate prerequisites without
using production credentials, NapCat runtime APIs or a renamed loader as a fallback.
