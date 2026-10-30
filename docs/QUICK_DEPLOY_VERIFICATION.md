# Windows / Linux quick deployment verification

Status (2026-10-30): **Windows now defaults to the native full runtime; its real
installation/startup/UI/model/QQ/lifecycle integration is NOT yet accepted.**
The image-build and isolated-container results below concern Linux/WSL only, not
the new native entrypoint. Unmodified Compose end-to-end deployment and real model
chat also remain **not yet accepted**. No production installation changed.

## Single implementation

- Linux: `bash ./install.sh` from the checkout now defaults to shared bootstrap `wizard`; explicit `up` retains terminal setup.
- Windows default: `install.cmd` uses PowerShell `-STA` and `tools/windows-native.ps1`
  (default `wizard`) to open the same setup UI for the native full host. Python
  3.12/3.13 is required, without WSL, Docker, Node or elevation. Missing Python can
  be installed with official user-scope winget only after explicit consent; mocked
  installer tests must never perform a host install.
- Explicit WSL compatibility remains in `install.ps1`, `tools/windows.ps1` and
  `tools/windows-setup.ps1`; these are not native commands and are not deleted.
  Existing WSL state is not automatically migrated. Lite remains separate.
- Explicit commands are wizard/init/doctor/up/info/stop/status/logs/health/chat. Existing private
  state is validated, not overwritten. Unmanaged directories and project ownership
  conflicts fail closed. Default full tool exposure remains unchanged.
- Linux/WSL `up` waits for Compose health; native `up` prepares private assets and
  waits for authenticated same-instance readiness. Both default to 300 seconds
  (configurable 1–3600); this is not a dependency-download/build deadline. Native
  `up` does not automatically upgrade installed source/plugins. Local readiness
  does not validate the model or QQ. No default QQ login is installed.

## Windows native full: provisional implementation (2026-10-30)

- Repository regression: **5,326 passed, 48 skipped, 460 subtests passed** on Windows; pyflakes and release validator passed (155 first-party files). Native lifecycle/dashboard/transport targeted regressions passed; the fresh-interpreter native setup served authenticated state without creating an installation. Skips include Unix-only/socket and unprivileged symlink boundaries. This is code-level evidence, not proof that an end-user install or QQ account works.
- The isolated actual Python 3.13 full dependency install is still resolving/downloading from official PyPI. This environment has slow direct public-network downloads; there is no success marker yet. No model keys, QQ accounts, existing deployment state or global pip packages are used. Full host smoke remains gated on that real installation completing.

- The core remains the same vendored AstrBot **4.28.1**, copied into private
  `runtime/astrbot`; the plugin is copied, not installed through admin symlinks.
  Full dependencies come from its `pyproject.toml` into `runtime/.venv`, never
  global pip. Only prebuilt Dashboard files are extracted from the official
  same-version PyPI wheel after fixed SHA256 + HTTPS metadata + byte checks; this
  is not an author signature. No Node build or different Python core is substituted.
- Default state is `%LOCALAPPDATA%\QhunlivBot\Full`, outside the checkout; new
  private directories use the current-user SID ACL without elevation. Loopback
  Dashboard candidate port 6185 and OneBot candidate 6199 are selected/saved at
  first initialization; a saved URL is not listening/readiness evidence.
- Native operator requests use an owned loopback endpoint, random token and SID
  ACL, not Linux root peer credentials. Full tools, persistent tasks, memory and
  the available-chat 60/40 source/summary split are retained. External VM/browser/
  mail/pointer bridges and Linux dependencies do not become Windows host tools.
  QQ still needs a manually configured external OneBot/NapCat service or client;
  nothing packages/downloads/logs into QQ automatically.
- Native `logs` reports only the private log path, not raw log contents. `stop`
  sends authenticated shutdown; the implementation drains accepted local and QQ/
  tool work before upstream core cancellation. Acceptance means the request was
  received, not that the process has exited. Detached operation after closing the
  setup window and graceful lifecycle still need real Windows smoke acceptance.
- Current native checks are limited to mocked boundaries and scoped Windows ACL /
  installer-step checks. A real private-venv dependency installation is in progress;
  it is **not recorded as passed here**. No accepted native full-host startup,
  rendered browser UI, real-model chat, QQ login/send-receive or stop/drain result
  is available yet. Historical Linux/WSL and synthetic-UI test counts below must
  not be reused as native acceptance. See [native deployment](WINDOWS_NATIVE_FULL.md).

## Historical Linux/WSL checks on 2026-10-30

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

## First usable dashboard turn (2026-10-30)

The out-of-box follow-up fixes a real request-lifecycle bug: plugin intake returned
before its background worker sent a WebChat reply; the pinned scheduler then ended
the HTTP response queue and late replies were discarded. WebChat now waits for its
owned worker without cancelling active tools on client cancellation. QQ intake
remains nonblocking. Independent dashboard conversations use isolated short hashed
private scopes, and concurrent WebChat request IDs are not coalesced into one
reply destination. Model loading honors the canonical runner selection retained by
the pinned upstream configuration migration, rather than only the removed legacy key.

[Actual isolated HTTP acceptance](evidence/webchat-first-turn-offline-20261030.json)
used the cached full runtime image, frozen current plugin source mounted read-only,
fresh tmpfs state, `docker run --network none`, and a deterministic loopback
synthetic OpenAI-compatible provider. No real model credentials, QQ sessions or
production data were used. Actual entrypoint startup, dashboard HTML 200, login,
session creation, chat SSE and persisted dashboard histories all ran without
framework mocks. A1/B1/A2 requests each received their structured reply before
terminal `end`; both conversation histories were isolated and A retained its prior
turn. The final frozen-source repeat passed with three provider requests and zero
provider failures. The runtime process group and test daemon were retired afterward.

This is **synthetic-model full-runtime HTTP integration**, not real-model acceptance,
JavaScript/browser rendering, unmodified Compose networking, public port access or
QQ transport coverage. The first attempt refused intentionally nonempty state from
a nested socket tmpfs; rerunning with empty whole-state tmpfs preserved the strict
fresh-state guard instead of weakening it.

First-run input validation now retries only a mistyped interactive field, including
full `/chat/completions` URLs, without re-entering correct values. API key input
fails rather than echoing on an insecure terminal. For fresh installs only, occupied
implicit default loopback ports can select and save free ports; explicit choices
fail before data writes, and existing installs never reallocate. Port availability
can still change between initialization and Docker binding. No foreign service is
stopped or reconfigured. Full regression on the frozen implementation: **4720
passed, 44 skipped, 348 subtests passed**. Raw plugin requests still use the globally
configured model; the dashboard's per-conversation model override is not yet honored.

## Zero-code visual onboarding (2026-10-30)

The WSL-compatible installer at this historical checkpoint provided Windows welcome/consent/distribution dialogs,
then a five-step browser wizard. The frontend is a fixed local page, not a modified
vendored dashboard or an alternate agent runtime. Initial configuration uses the
same exclusive bootstrap writer; lifecycle actions invoke the same owned CLI;
explicit consented chat uses the same full local socket path. Existing state is
immutable to first-run saves, model/QQ checks are not implied by local readiness,
and closing setup never stops an already-running Bot.

- Real loopback HTTP tests cover exact Host/Origin/Bearer, duplicate headers/JSON,
  invalid framing/bodies and fixed secret-free errors. Unauthorized small request
  bodies are safely discarded with a short deadline before 403 to avoid Windows
  connection resets; rejection never dispatches an action.
- Real Chromium tests exercise welcome, environment check, model/role save,
  start/health, one consented chat, failed-input correction, duplicate-submit
  prevention, console links, explicit credential reveal/30-second hiding,
  existing-install reopening and mobile no-overflow. Runtime commands/provider
  responses are synthetic; all browser traffic is restricted to the test listener.
- A fresh Python child starts the real `bootstrap.py wizard --no-browser` from an
  unrelated working directory; authorized HTTP works and no state is created
  before save. Only that owned child is terminated by the test.
- Real Windows PowerShell 5.1 parsing and mocked WSL/browser boundary tests cover
  the native UI script's UTF-8 Chinese, consent/cancellation and same-instance
  forwarding. Actual WSL installation / desktop dialog click-through is not
  accepted by those mocks. Prerequisite-install progress still uses the system
  installer / terminal; it is not represented as a fake frozen progress window.
- Actual WSL Python/Bash stdlib launcher suite: **6 tests passed** for default
  wizard, explicit up compatibility, exact argv, stdin, cwd and exit codes. No
  Docker daemon, model, QQ login, production state or installation was touched.

Final frozen-source regression: **4910 passed, 45 skipped, 442 subtests passed**.
Targeted wizard/bootstrap tests: **145 passed**; scoped Windows tests: **99 passed,
13 subtests passed**. Static `pyflakes`, Python AST/expanded release validation
and `git diff --check` passed. The extra Windows skip is the added Linux-only
explicit-up compatibility case, separately run in the actual WSL suite above.

The user guide is [visual setup](VISUAL_SETUP.md). This acceptance is UI and
entrypoint coverage, not unmodified Compose networking, paid-model or QQ transport
acceptance. Those outstanding boundaries remain below.

## Remaining acceptance

For Windows native, finish the real private-venv install, owned host startup,
loopback Dashboard rendering/login and same-instance chat acceptance; separately
verify current-user ACL fail-closed behavior, detached window-close survival and
authenticated drain/stop completion with accepted local/QQ work. Real model and
QQ acceptance need authorized services and remain distinct. Do not perform a host
Python install during automated tests or claim all external capabilities work.

For the explicitly retained Linux/WSL compatibility entrypoints, still verify
unmodified Compose networking,
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
