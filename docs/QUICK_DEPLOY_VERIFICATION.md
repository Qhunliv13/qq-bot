# Windows / Linux quick deployment verification

Status: launchers/preflight, complete image builds and isolated container health/
restart checks passed. Unmodified Compose end-to-end deployment and real model
chat **not yet accepted**. No production installation changed.

## Single implementation

- Linux: `bash ./install.sh` from the checkout; defaults to shared bootstrap `up`.
- Windows: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1`;
  defaults to `up` through the WSL launcher, not another installer implementation.
- Explicit commands are init/doctor/up/info/stop/status/logs/health/chat. Existing private
  state is validated, not overwritten. Unmanaged directories and project ownership
  conflicts fail closed. Default full tool exposure remains unchanged.
- `up` waits for Compose health with a 300-second default (configurable 1–3600).
  Success describes local runtime readiness only. No default QQ login is installed.

## Actual checks on 2026-10-30

- Windows PowerShell 5.1 tests use a substituted WSL boundary for argv, space paths,
  default command, stdin, exit-code forwarding and invalid argument rejection.
- Five Linux stdlib tests passed in Ubuntu-TSC: four execute real Bash against a
  private fake-Python fixture, plus source/LF validation. No Docker is used by them.
- With user approval, Ubuntu packages docker.io 29.1.3, Compose 2.40.3 and Buildx
  0.30.1 plus dependencies were installed (10 new packages, none upgraded/removed).
  Maintainer scripts were reviewed; temporary exclusive policy-rc.d denied service
  startup and was then archived, not deleted. This WSL does not run systemd as init.
- A dedicated Docker daemon used its own Unix socket, data/exec roots and PID file.
  It was launched with bridge disabled and iptables/ip6tables/IP-forward/IP-masq
  management disabled; no default Docker service or production daemon was adopted.
- [Linux preflight evidence](evidence/quick-deploy-preflight-evidence.json): actual root Bash
  launcher init/init-again/doctor/status all exited 0 against that daemon, private
  settings mode was 0600, and settings bytes were unchanged. Settings contained a
  deliberately fictitious example.invalid model and noncredential test key; no
  model call was made.
- Actual root Windows PowerShell launcher → selected Ubuntu-TSC WSL → Docker doctor
  exited 0. Only nonsecret Docker/port environment names were forwarded for this
  explicit lab invocation; Windows API keys are not automatically forwarded.
- Actual full Dockerfile build was attempted with host build networking on the
  dedicated daemon. Docker Hub node:20-bookworm-slim manifest retrieval timed out;
  Python base-image resolution was cancelled. No project build step or container
  startup completed. No alternate registry, insecure TLS or DNS override was used.

## Actual image and isolated runtime acceptance (2026-10-30, follow-up)

The earlier metadata failure is retained above as historical evidence, not the
current build outcome. Windows had an existing loopback HTTP proxy; direct Windows
and WSL registry access failed. With explicit user approval, a temporary relay bound
only the Windows WSL interface and admitted only the verified test WSL source IP.
TLS validation remained enabled; no alternate registry, DNS or firewall change was
made. The proxy had to be supplied to the Docker CLI (anonymous token retrieval),
the dedicated daemon and build steps. Both complete image builds then exited 0.

Actual frontend compilation exposed a missing sibling Shiki asset. The Dockerfile
now copies the exact vendored asset before building; no vendor bytes were changed.
Health now reuses the local socket client and additionally requires bounded HTML
from the fixed loopback dashboard, with proxies and redirects disabled. Compose
uses root-only tmpfs for the socket directory, preserving fail-closed bind behavior
without carrying stale sockets across container stops.

[Isolated container evidence](evidence/quick-deploy-container-evidence.json) records the fixed
image's actual composite health, Shiki artifact, root:0700 run directory, forced
kill/start recovery, graceful stop/start recovery, unchanged settings/framework
configuration and final stopped state. This was **docker run --network none**, not
an unmodified Compose deployment or host-port test. The baseline image first
initialized the same private test state; the fixed image exercised preserved state.
Upstream online metadata lookup errors were expected with networking disabled;
no real model request or QQ login was performed. Both lab containers are stopped
and retained. The dedicated daemon subsequently exited 0 gracefully; the temporary
proxy relay was cancelled after builds completed. Full regression after source
freeze: **4485 passed, 44 skipped, 348 subtests passed**.

## Open-source onboarding follow-up (2026-10-30)

- Shared bootstrap now exposes `info` without Docker or network access; default output and `info --json` do not include model keys or passwords. `info --show-password` reveals only the dashboard password by explicit local request, and cannot be combined with JSON or unrelated commands.
- New initialization saves deployment ports once; later shells use them without re-exporting environment variables. Explicit environment overrides are temporary. Old settings remain valid and are not rewritten. Windows browser launch reads the secret-free information from the same Linux data directory instead of assuming a Windows-side default port.
- `stop` checks ownership of the selected Compose project and issues `compose stop`, not `down`, deletion or reset. Tests reject another installation's project before any stop action; no live service was stopped for this feature's test.
- Real PowerShell tests cover copied Chinese/spaced checkouts, Unicode Linux HOME, multi-distro selection/cancellation, unsupported WSL list commands, prerequisite diagnostics and same-instance command forwarding. They replace WSL/install/browser boundaries; they do not prove a complete Windows first installation.
- Actual WSL Bash launched `init`, `info`, `info --json` and explicit password retrieval from `/tmp` against disposable external state with spaces. Saved non-default ports survived removing environment overrides; settings bytes and restrictive permissions were unchanged. No Docker daemon, model or QQ connection was needed or used.
- User-facing README, quick start and platform guides now lead with installation, three model inputs, access recovery and common operations. Existing detailed capabilities remain in [feature details](FEATURES.md); advanced evidence is not presented as onboarding.
- Full regression for this onboarding pass: **4624 passed, 44 skipped, 348 subtests passed**; final targeted launcher/configuration follow-up: **147 passed**. Follow-up account-change regression and output wording explicitly distinguish initial credentials from later dashboard changes; `info` does not read, reset or claim to recover the current dashboard password. This remains offline/interface coverage, not complete first-install/model/QQ acceptance.

## Remaining acceptance

Still verify both quick-launch entrypoints through unmodified Compose networking,
host loopback dashboard access and explicit same-instance chat with authorized
model access. The isolated image run above does not prove those outcomes. After verification, the dedicated daemon was sent
SIGTERM only after its PID command line matched the private data root; it exited
0 with graceful shutdown. Private test state is retained, not deleted. Final full
regression: 4478 passed, 44 skipped, 338 subtests passed. Linux-only launcher cases
were separately exercised under real WSL Bash rather than treated as Windows skips.
Test-daemon bridge-disabled configuration is a lab
containment choice, not the recommended production Compose networking setup.

Independent native QQ login/send-receive and GUI live acceptance remain separate
unfinished work. See [native acceptance status](QQ_NATIVE_ACCEPTANCE_STATUS_20261030.md).
