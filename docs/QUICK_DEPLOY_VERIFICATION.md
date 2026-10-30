# Windows / Linux quick deployment verification

Status: launchers and preflight implemented and exercised; full image build and
healthy container startup **not yet accepted**. No production installation changed.

## Single implementation

- Linux: `bash ./install.sh` from the checkout; defaults to shared bootstrap `up`.
- Windows: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1`;
  defaults to `up` through the WSL launcher, not another installer implementation.
- Explicit commands remain init/doctor/up/status/logs/health/chat. Existing private
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
- [Linux preflight evidence](quick-deploy-preflight-evidence.json): actual root Bash
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

## Remaining acceptance

The same Dockerfile must complete its frontend and Python build with reachable
trusted image/dependency registries. Then verify the unmodified Compose deployment,
loopback dashboard, local-socket health, restart/config preservation and explicit
same-instance chat with authorized model access. Current tests do not prove these
container/runtime outcomes. After verification, the dedicated daemon was sent
SIGTERM only after its PID command line matched the private data root; it exited
0 with graceful shutdown. Private test state is retained, not deleted. Final full
regression: 4478 passed, 44 skipped, 338 subtests passed. Linux-only launcher cases
were separately exercised under real WSL Bash rather than treated as Windows skips.
Test-daemon bridge-disabled configuration is a lab
containment choice, not the recommended production Compose networking setup.

Independent native QQ login/send-receive and GUI live acceptance remain separate
unfinished work. See [native acceptance status](QQ_NATIVE_ACCEPTANCE_STATUS_20261030.md).
