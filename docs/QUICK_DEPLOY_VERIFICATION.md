# Windows / Linux quick deployment verification

Status (2026-10-31, Asia/Shanghai; fresh-install evidence baseline `15faee1`): **Windows native fresh-directory Python 3.12.10 installation through the unmodified default product asset path and its isolated full-host lifecycle passed on the current host.** The new private root was secured before asset preparation, with a fresh private pip cache and no payload/cache preseeding, monkeypatch, recovery adapter, proxy or TLS changes. All five asset checks passed; all **62 applicable** unchanged AstrBot 4.28.1 requirements match and `pip check` passed. The earlier installed Python 3.13 fixture remains a separate accepted record with **63 applicable** requirements and test-assisted download recovery; its historical default-download failures are retained below.

The new Python 3.12 full host passed Dashboard HTML/password API login, authenticated same-instance synthetic-model chat, in-flight reply/drain with actual process exit, detached CLI-parent survival and persisted-state restart. Actual Windows PowerShell 5.1 wrapper lifecycle also passed against that new instance. Real Chromium runs also drove the actual Python 3.12 setup subprocess and full host without controller/runtime substitutes: both existing management and a corrected fresh form-save/default-install flow passed environment check, start, consent-gated visible reply and confirmed stop/exact exit. Prior Chromium Dashboard login-page rendering/login remains a separate accepted record; the new run covered the setup page, not a new 3.12 Dashboard browser login. **This current-host default-path success is not a universal network guarantee or brand-new-PC GUI/double-click acceptance.** Missing-Python winget installation, all Dashboard pages, whole-terminal-close/logoff survival, real provider/QQ/external-tool drain and unmodified Compose end-to-end deployment remain unverified. No global installation, vendor source or production instance was modified; asset installation used no model/QQ settings.

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

## Windows native full: current-host fresh installation and lifecycle (2026-10-31)

[Secret-free native acceptance evidence](evidence/windows-native-full-evidence.json) records public installed requirement versions and fixed host/browser/launcher results, without private paths, tokens, chat payloads or raw logs.

- Final full regression **after the Windows wildcard-listener port-selection fix**: **5,424 passed, 50 skipped, 460 subtests passed in 376.06s** on Windows. Six real-listener regressions were added, and an older info test now asserts the saved port instead of assuming 6185 is free. The previous installer-deadline baseline was 5,418/50/460. All-scope pyflakes and the release validator passed (**156 first-party files**, no failures). The prior interpreter-selection result was 5,381/49/460 and private-cache baseline `97cba97` was 5,365/49/460. Focused launcher/assets/runtime verification previously passed **185 tests, 4 skipped**. Skips include Unix-only/socket, real Windows symlink boundaries and the non-Windows-only supervisor check; this is not clean-PC or external-service acceptance.
- **New current-host fresh-directory default asset path passed** (2026-10-31, Asia/Shanghai; collected job `pwsh-2fdf06574b974ed1`): a new private root was secured **before** preparing assets. Actual Python **3.12.10** ran unpatched `native_assets.prepare_assets` with a fresh instance-private pip cache, no payload/cache preseeding, no monkeypatch/recovery adapter, and no network/proxy/TLS changes. The unchanged vendored AstrBot **4.28.1** full requirements, copied core/full plugin and official same-version Dashboard wheel (**7,063,082 bytes**, fixed SHA256 verified through the actual default HTTP path) completed under the existing **1800s per-step** policy. All five checks—`setup_cleanup`, `source`, `venv`, `dependencies`, `dashboard`—were true. No global packages, vendor or production files changed; asset installation did not use model/QQ settings. This supersedes the earlier current-status “default path not passed” conclusion **for this host and fresh directory**, not for every network or brand-new computer.
- **New Python 3.12 requirement and full-host acceptance passed** (collected job `pwsh-4f949e55f800456f`): all **62 applicable requirements** exactly checked against the unchanged vendored pyproject matched; `audioop-lts` was correctly excluded by its Python-version marker on Python <3.13, **not removed** from requirements. `pip check` passed; no range/cache-recovery adapter was used. That same fresh-installed full core/plugin/venv served Dashboard HTML, initial-password API login and authenticated local health. The actual full model/tool loop returned visible text against a synthetic localhost OpenAI-compatible provider (**two model requests**, no external model/QQ). During the second held in-flight request, authenticated stop was accepted and the exact process HANDLE confirmed it remained alive; release produced the visible in-flight reply and then actual process exit. An independent CLI parent exited while the host survived; restarting the same persisted state produced a new PID and authenticated stop again reached actual exit. All fixture hosts are stopped.
- **New Python 3.12 actual PowerShell 5.1 wrapper acceptance passed** (collected job `58c4b07ae3e34550`): actual wrapper `up` and `health` reported the same host PID; the host survived CLI-parent exit, and authenticated wrapper `stop` plus an OS process wait proved actual exit. No model/QQ calls occurred in this wrapper run; all hosts are stopped. This verifies the new fixture's wrapper lifecycle, **not brand-new-PC GUI/double-click, missing-Python winget installation, whole terminal close or logoff**.
- **Real Python 3.12 setup-browser/full-host management passed** (collected job `pwsh-4084ce097a2e47c1`): Chromium opened an actual `native_runtime.py wizard` subprocess for the same already default-installed fixture, with **no controller, command, model-client or runtime substitution**. The UI entered existing-install management, erased the URL capability fragment, preserved the settings byte-for-byte, performed actual environment check and started the complete host. Submitting without per-turn consent caused **zero model requests**; confirmation yielded **one localhost synthetic-provider request** through the real full plugin loop and a visible reply, then cleared consent. QQ stayed unverified. Cancelling Stop caused no stop action; confirming Stop reached actual exit proven by a held exact OS process HANDLE. Browser page/console errors and nonlocal browser requests were **zero**, storage/cookies remained empty and known settings secrets were not visible. The idle directly owned setup interpreter was ended only after host exit. No packages, external model or QQ were used. This is **actual existing-instance setup UI linkage**, not fresh form-save/first download in the browser, automatic Windows browser launch or brand-new-PC/double-click acceptance.
- **Fresh-browser first-install collision found and fixed**: the first full form-save/default-install attempt (`pwsh-521a9d7070bc4207`) completed dependencies, `pip check` and Dashboard, but upstream startup rejected a selected default port already held by an older unrelated service. The test did **not** pass and no model reply was claimed. A real local reproduction proved that on Windows an exclusive loopback bind can succeed beside a preexisting nonexclusive wildcard listener; the previous first-port check therefore saved an occupied port. The shared first-install selector now probes with an exclusive wildcard binding **without listening/connecting**, while the actual Dashboard stays loopback-only. Implicit defaults may choose free ports; explicit occupied ports still fail; existing installations are never silently reassigned. **138 relevant tests passed**, and real exclusive/nonexclusive wildcard/loopback listener cases confirm no existing service is stopped. The failed fixture is retained; the existing listener was not stopped and its settings were not changed. The failed test was unwound by ending only its verified disposable Playwright driver; its owned wizard then exited. A new blank fixture subsequently **passed the corrected complete browser first-install flow** as recorded next.
- **Actual fresh-browser first-install/full-host flow passed** (collected job `pwsh-b61a8df2255445cd`): one new absent data root was initialized only by real Chromium form submission to the real Python 3.12 wizard; the actual environment check created no state. It saved synthetic localhost model settings and chose an unused default port without touching the preexisting listener. Clicking Start then installed full dependencies, core/plugin and pinned Dashboard from the unmodified default paths with **no preseeded venv/cache/assets, transport patches or runtime/controller/client substitutes**. Actual authenticated readiness preceded browser trial chat; without consent **zero model calls**, with consent **one localhost synthetic request and visible full-loop reply**. Consent reset, QQ stayed unverified, cancelling Stop did not act and confirmed Stop reached exact-handle actual process exit. Settings were unchanged after save; page/console errors and browser nonlocal requests were zero, browser storage/cookies empty, known secrets not visible. Subsequent exact-venv check matched **62 applicable dependencies**, skipped `audioop-lts` by its legitimate marker, and passed `pip check`; private root/cache ACLs, assets, removed guard/endpoint and stopped Dashboard were verified. Follow-up ports/bootstrap/native/controller regression passed **228 tests**. This closes **current-host browser form-save → first network install → full host → confirmed synthetic chat → stop** acceptance; it does **not** test Windows automatic browser launch, downloaded-ZIP MOTW/SmartScreen, missing-Python winget or a genuinely clean computer.
- **Source ZIP structure passed** (job `pwsh-ad937c1c14d7441f`): Git source export excluded historical trash and `.git`, retained all required native/vendored files and all **63 requirement declarations**, and preserved CMD CRLF. After extraction into a Unicode/space path, actual PowerShell 5.1 `doctor` passed without creating data state or installing packages. This checks export layout/path handling, **not browser-downloaded MOTW/SmartScreen, Explorer double-click or a clean PC**.
- Private retry-cache integration: **184 passed, 4 skipped** across assets, native lifecycle, setup controller and real Chromium. Product baseline `97cba97` changes only private dependency caching: the instance-owned `runtime/pip-cache` is guarded by the shared Windows private-leaf helper. Tests retain a simulated complete cache entry across an incomplete-venv retry, preserve all vendor requirements and leave global cache untouched; **full-install cache reuse remains unverified**; a later real small-wheel cache check is recorded separately below. The four skips are real Windows symlink-creation cases, not simulated reparse/unsafe-cache refusals.
- **Real bounded private-cache follow-up passed** (jobs `pwsh-e6007c3ae77e46aa` and `pwsh-e69cb06ee8f14b25`): a fresh exact private root/cache downloaded the official **filelock 4.2.4 wheel, 162,475 bytes**, using unpatched default pip transport. The second serial download explicitly reported its cached wheel and matched the first file's bytes/SHA256 and exact official release metadata. A new private venv then installed that single requirement through the product subprocess boundary and passed `pip check`. No package was installed globally, no model/QQ credentials were used, and strict root/cache SID ACLs passed. This is a small-wheel cache/retry check, **not a large-download fix or full first-install pass**.
- **Historical bounded large-wheel default download did not pass** (job `pwsh-a62a17fb8f154d56`): one unpatched official pip download of FAISS 1.15.1 (expected Windows CP313 wheel **16,292,975 bytes**) into the same private cache did not finish within its **90s test wall bound**. Only the directly created download child was ended and collected; no package was installed. This bound is test-only, not a new product timeout, and the result does not identify the complete network root cause. Do not repeat the same long probe or describe private caching as a fix for the observed large-response stall.
- **Equal-byte HTTPS comparison** (job `pwsh-c7303649a3f84f8d`) read exactly the same first **1 MiB** of that official FAISS artifact in three bounded response modes: ordinary GET prefix **8.812s**, one 1-MiB range **2.627s**, and sequential 64-KiB ranges **16.552s**; all three prefix SHA256 values matched. This single concurrent comparison cannot establish a permanent networking root cause, prove small ranges generally faster, or turn the 90s incomplete full download into a deadlock diagnosis. No pip, proxy, TLS or global configuration was changed. The separate fresh Python 3.12 default asset preparation later completed successfully as recorded above. The earlier 90s incomplete attempt and this comparison remain time-specific observations; the later pass does not establish that every network is fixed.
- **Contained installer deadline follow-up passed**: asset steps now use a configurable per-step **1800s default** (`BOT_NATIVE_SETUP_TIMEOUT`, 1–86400s) on Windows 10/Server 2016+. One shared supervisor creates an atomically assigned, noninheritable kill-on-close Job and inherits only fresh NUL stdio; no post-start PID assignment, shell, process-name kill or pip-internal adapter. It requires root exit **and zero job processes** before success, rechecks the deadline, and terminates only this owned job on timeout. A nonsecret review guard is armed before spawning and removed only after confirmed cleanup; parent death or uncertain teardown blocks subsequent preparation until ownership review. It never auto-retries, deletes the venv/cache, or writes dependency completion before full install plus `pip check`. This bounds installer child steps, not asset HTTP or running Bot/model/tool work.
- **Actual product runner smoke passed** (job `pwsh-783b4e4356594f91`): a 3s local fixture timeout ended its root/child, proven with held OS handles; the cache survived, no completion marker was written, and confirmed cleanup removed the guard. A subsequent explicit step ran `pip check` in a real private venv successfully. No network/package install/model/QQ action occurred. **221 integration tests passed, 5 skipped**, including real grandchild cleanup, supervisor death through a venv redirector, Unicode environment/paths, handle isolation/leaks, injected cleanup uncertainty, guard failures, late-deadline rejection and wizard busy release. Pyflakes and release validation passed (**156 first-party files**). A post-change actual PowerShell launcher lifecycle run (`pwsh-6f91939a5b8048e7`) again passed up/health, CLI-parent survival and authenticated stop/exact-process exit without reinstalling the full venv. This bounds installation waits, **not a download-performance fix**; the later independent Python 3.12 current-host default-path pass is recorded separately above.
- Real Chromium setup presentation: **8 passed** for both container and Windows-native text, consent, configuration preservation and model-call substitutes. This is actual setup DOM/JS/HTTP coverage, not browser-rendered AstrBot Dashboard, full package installation, a real provider or QQ.
- Actual isolated Python **3.13** full installation passed: all **63 applicable requirements** satisfy the unchanged vendored AstrBot **4.28.1 `pyproject.toml`**, and **`pip check` passed**. Assets are ready, including the official same-version AstrBot wheel (**7,063,082 bytes**) verified against its fixed SHA256 and its prebuilt Dashboard. Neither dependencies nor the core were reduced or replaced.
- **Historical Python 3.13 default-network first attempt did not pass.** The original exact owned pip child stalled in SSL/socket body reads and was safely ended; 48 complete wheels were preserved and matched official filename/length/SHA256. The roughly 19 MB jieba archive and Dashboard wheel were recovered with official **64 KiB HTTPS ranges**. Final completion used external test-only `native_fixture_pip.py` (stdlib HTTPS, 64 KiB chunks, eight threads, exact official file host/Content-Range/length checks, unchanged pip link-hash verification) and `native_full_install_retry.py`. Neither adapter is shipped or wired into the product; their success does not validate default large-file networking or full-install cache reuse. The separate unpatched small-wheel cache evidence above does not resolve those boundaries.
- **Earlier Python 3.13 installed-host smoke passed** (job `pwsh-11cf492cf5664a67`): unchanged AstrBot 4.28.1, copied full plugin and a private venv served loopback Dashboard HTML, initial-password API login and authenticated local health. The actual same full model/tool loop returned visible text against a synthetic localhost OpenAI-compatible provider (**two model requests total**, no external model/QQ). With the second model request held in flight, authenticated stop was accepted; waiting on the exact process HANDLE proved it remained alive while processing. Releasing that request yielded the visible in-flight reply, then actual process exit. An independent short-lived CLI parent exited after `up` while host health survived; restart reused persisted state with a new PID, and authenticated stop again reached actual exit. All test hosts are stopped.
- **Actual Dashboard browser login passed** (job `410e1cb43f454865`): Chromium rendered the pinned Dashboard login form, made **42 local JavaScript requests**, submitted the initial password successfully (login POST 200/token and navigation away from login), and recorded **zero page JavaScript errors**. Two nonlocal requests were blocked; no model or QQ calls were made. Authenticated stop was followed by exact-process wait confirming exit. This verifies login rendering/navigation, not all Dashboard pages or interactions.
- **Actual Windows PowerShell 5.1 wrapper lifecycle passed** (job `pwsh28a035e48f3547c6`) after correcting interpreter selection: on a host with both Python 3.12 and 3.13, the wrapper selected the installed instance's existing 3.13 rather than newly preferring 3.12. Actual `windows-native.ps1 up` returned with its CLI parent exited while native health survived; wrapper health matched the host PID, and wrapper stop plus an OS process HANDLE wait confirmed actual exit. No model/QQ call was made and the host is stopped. This is installed-instance wrapper acceptance, not fresh downloads, winget installation or new-PC double-click; the separate post-fix full-suite result is recorded above.
- The earlier Python 3.13 populated fixture's initial root `icacls` operation timed out at **2s before secrets were written**. A privacy operation limited to that exact known fixture, bounded at **60s**, then verified strict current-user SID ACLs before host smoke; no product ACL was weakened. The new Python 3.12 fresh root was instead secured before asset preparation as recorded above. Runtime keys were random synthetic local-only values, not production credentials. No private paths, raw logs, chat payloads or secret values are published.

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
  sends authenticated shutdown and drains accepted work before core cancellation.
  A stop acknowledgement alone still does not prove exit: the real smoke above
  separately verified in-flight local reply completion and exact-process exit.
  Detached CLI-parent survival passed; closing the actual desktop setup window
  and draining real QQ/external-tool operations remain separate unverified cases.
- Native evidence now has **two distinct installation records**: the earlier Python
  3.13 private venv with test-assisted artifact retrieval, and the new Python
  **3.12.10 fresh-directory full installation through the unmodified default product
  path** on the current host. Each has full-host lifecycle acceptance in its stated
  synthetic-local-provider scope. Neither proves universal network reliability,
  brand-new-PC desktop/double-click onboarding, consented missing-Python winget
  installation, all-page Dashboard interaction, whole-terminal-close/logoff
  survival, external-provider protocols/models, QQ login/send-receive or real
  QQ/external-tool drain. VM/mail/browser services and Linux-only bridges remain
  separate. Historical Linux/WSL counts are not native acceptance. See
  [native deployment](WINDOWS_NATIVE_FULL.md).

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

For Windows native, the new Python **3.12.10 fresh-directory default product asset
path and full-host lifecycle passed on the current host**; the earlier Python 3.13
installed-host/test-assisted retrieval record remains separate. Both lifecycle
records use synthetic localhost providers, not external model or QQ services.
Still verify brand-new-computer GUI/double-click end-to-end onboarding, consented
missing-Python winget installation (no host-install authorization was given),
full-install cache reuse beyond the verified small wheel, all-page Dashboard
interactions, actual setup-window-close/whole-terminal-close/logoff survival,
external-provider protocol/model coverage, real QQ login/send-receive and real
QQ/external-tool drain. VM/mail/browser/Linux-only bridges require separate
authorized acceptance. Actual PowerShell 5.1 wrapper lifecycle is accepted for both
recorded instances, not complete new-PC onboarding. The default-path pass is
host/directory/time-specific; historical failures remain valid and no all-network
reliability claim is made. Do not install host Python during automated tests or
describe remaining boundaries as passed.

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
