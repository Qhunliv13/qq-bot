# Linux quick deployment

## What this starts

The root [install.sh](../install.sh) is a thin Bash entry point for the shared
[bootstrap](../tools/bootstrap.py). With **no arguments**, it runs `up`; otherwise
it forwards every argument unchanged. It preserves the caller's working directory,
standard input and the Python process's exit status. Quote paths containing spaces.
Use Bash, not `sh`. The examples use `bash install.sh`, so a missing executable bit
on a Windows checkout does not prevent launch. The file must remain UTF-8 with LF
line endings.

The default build starts the **full bundled AstrBot runtime and dashboard**, plus
this project's plugin, in Docker. It is not a minimal replacement server. The first
build downloads images and dependencies and can take time. **NapCat is not bundled
or installed. Starting successfully does not log into QQ, configure an external
OneBot adapter, or prove that a model API works.** QQ integration is a separate,
explicit task; no message is sent to QQ by these setup/health commands.

## Prerequisites (install manually)

- A Linux host, Bash, and `python3` (host Python **3.10+ recommended**). The bootstrap
  uses only the Python standard library; no host `pip install` is needed. This is
  distinct from the bundled AstrBot project's **Python >=3.12** requirement: the
  [Dockerfile](../deploy/Dockerfile) supplies Python 3.12 inside the container.
- Docker Engine running and accessible to the current user, with the **Compose v2
  plugin** (`docker compose`, not the legacy `docker-compose` command).
- A complete checkout, including the vendored AstrBot source; sufficient disk space
  for the full image build, and network access for the build and chosen model API.
- An OpenAI-compatible base URL, a valid model ID, and an API key.

Use Docker's official, distribution-specific package-repository instructions:
[Docker Engine installation](https://docs.docker.com/engine/install/) and
[Compose plugin installation](https://docs.docker.com/compose/install/linux/).
For Ubuntu see [the Ubuntu Engine guide](https://docs.docker.com/engine/install/ubuntu/).
Review and run the applicable package commands yourself; do not pipe a downloaded
installer into a shell. The launcher does **not** install Docker, invoke `sudo`,
change firewall rules, or restart host services. Docker access is privileged;
consult [Docker's Linux post-install guidance](https://docs.docker.com/engine/install/linux-postinstall/)
before granting group membership, or use the documented rootless configuration.

Verify the prerequisites manually (these commands do not start a bot):

```bash
bash --version
python3 --version
docker --version
docker compose version
docker info --format '{{.ServerVersion}}'
```

If Docker access fails, resolve it using the official instructions; the launcher
will not attempt an automatic privilege escalation or daemon repair.

## Choose isolated state, then start

Use a **new, dedicated directory outside the checkout**, not an existing production
bot directory, and reuse exactly that directory for all subsequent commands. The
bootstrap refuses an unmanaged nonempty directory rather than importing or
rewriting it. The default state location is `$HOME/.local/share/qhunliv-bot`; if it
might already be in use, do not use the no-argument shortcut for a fresh instance.

The commands below assume the checkout is your current directory. Choose a unique
state directory and runtime name for this installation:

```bash
BOT_STATE="$HOME/.local/share/qhunliv-bot-linux-demo"
BOT_NAME="qhunliv-bot-linux-demo"
bash install.sh init --data-dir "$BOT_STATE" --runtime-name "$BOT_NAME"
bash install.sh doctor --data-dir "$BOT_STATE" --runtime-name "$BOT_NAME"
bash install.sh up --data-dir "$BOT_STATE" --runtime-name "$BOT_NAME"
```

`init` prompts for the model URL/ID and a hidden API key, and optionally QQ owner
IDs. Supplying owner IDs alone does **not** enable a QQ adapter or log into QQ.
`init` writes private external configuration without Docker. `doctor` checks
prerequisites/configuration read-only; it does not build or start services. `up`
initializes if needed, checks prerequisites, then builds and starts the full runtime.
It uses Compose's health wait before reporting success: the default readiness wait
is **300 seconds**, configurable with `--wait-timeout` (integer seconds, 1–3600).
This wait is for runtime health, **not** QQ login or a successful model API request;
it is not a total timeout for downloading dependencies or building the image.
For a slower first startup you can allow 600 seconds:

```bash
bash install.sh up --data-dir "$BOT_STATE" --runtime-name "$BOT_NAME" --wait-timeout 600
```

If startup fails or health waiting times out, inspect `status` and `logs` for that
same state/name/port selection before retrying. Do not repeatedly tear down or
recreate the runtime, delete its state, or use `down` as a health-timeout remedy.

For an unused default state directory the shortest interactive command is simply:

```bash
bash install.sh
```

For invocation from another directory, use the absolute launcher path, for example
`bash "/home/me/QQ Bot/install.sh" status --data-dir "$BOT_STATE" --runtime-name "$BOT_NAME"`.
Relative paths such as `--persona-file ./persona.txt` resolve from your current
working directory, not from the checkout. Options do not imply `up`: use
`bash install.sh up --data-dir ...`, not `bash install.sh --data-dir ...`.

The dashboard is loopback-only at **http://127.0.0.1:6185** by default. Its generated
password is stored in the selected external state's `settings.json`. Keep that file
private; do not commit it, print it into logs, or share it. For a remote host use an
SSH tunnel or a separately reviewed access setup; do not open the firewall or bind
the dashboard publicly just to follow this guide.

Unique state/project names do not by themselves prevent host-port collisions.
The [Compose file](../deploy/compose.yaml) currently uses `BOT_WEB_PORT` (default
6185) and `BOT_ONEBOT_PORT` (default 6199), both bound to `127.0.0.1`. The selected
ports must be valid and distinct. Select unused ports before launching a second
instance, and keep those values consistent for all
commands for that instance. For example, after checking that 16185 and 16199 are
free, export both values **before** `doctor`/`up` and keep them set for subsequent
commands (the current interface does not persist these exports):

```bash
export BOT_WEB_PORT=16185
export BOT_ONEBOT_PORT=16199
bash install.sh doctor --data-dir "$BOT_STATE" --runtime-name "$BOT_NAME"
bash install.sh up --data-dir "$BOT_STATE" --runtime-name "$BOT_NAME"
```

With that override, open http://127.0.0.1:16185 rather than the default URL. An
exposed OneBot port does not mean an adapter is configured or QQ is logged in.

## Check and use the same instance

Keep `BOT_STATE` and `BOT_NAME` set to the original values (or substitute them
explicitly if opening a new terminal):

```bash
bash install.sh status --data-dir "$BOT_STATE" --runtime-name "$BOT_NAME"
bash install.sh health --data-dir "$BOT_STATE" --runtime-name "$BOT_NAME"
bash install.sh logs --data-dir "$BOT_STATE" --runtime-name "$BOT_NAME"
```

- `status` reports Compose container state, not an end-to-end model test.
- `health` checks the existing runtime's local client/socket readiness without
  sending a model or QQ request. Startup can take time; inspect logs if not ready.
- `logs` follows the last 100 lines; Ctrl+C exits the log viewer, not the bot.
  Treat runtime logs as private and redact before sharing.

After readiness succeeds, send one deliberate model request through that same
running bot (this may incur your model provider's normal cost):

```bash
bash install.sh chat --data-dir "$BOT_STATE" --runtime-name "$BOT_NAME" --prompt "Hello from Linux"
printf '%s\n' 'A prompt supplied on standard input' | bash install.sh chat --data-dir "$BOT_STATE" --runtime-name "$BOT_NAME"
```

Chat success verifies that request only; it does not establish QQ connectivity.
Never put API keys into `--prompt`, the command-line arguments, or pasted logs.
For unattended initialization use the shared bootstrap's `--api-key-env VARIABLE`
option with a securely supplied environment variable; pass the variable **name**,
not the key. Interactive hidden entry is preferred for a manual installation.

## Configuration is preserved

Repeating `init` validates the managed state and leaves existing settings/secrets
unchanged. Repeating `up` reuses that external state; it is not a reset or migration
command. Setup flags are not an overwrite interface for an existing installation.
Keep the original runtime name, state directory and port selection. A marker/name
mismatch or an unmanaged state directory is a safety failure: inspect the choice,
do not delete the marker or repoint the command at production state to bypass it.
Back up private external state before intentional configuration maintenance.

## Offline launcher tests (no Docker required)

The single shared [unittest fixture](../tests/test_linux_launcher.py) uses real Bash
and a fake `python3` executable in a private Linux `/tmp` directory. It verifies
space-containing checkout paths, unrelated caller cwd, exact arguments (including
shell syntax and Unicode), stdin preservation and nonzero child exit codes. It
never invokes Docker, starts a runtime, installs packages, or accesses the network.

On Linux, no pytest installation is necessary:

```bash
python3 tools/test_linux_launcher.py
```

On Windows, the ordinary test collection runs the source checks and deliberately
skips real-Bash tests. Run the same stdlib fixture explicitly in the existing WSL
distribution, adjusting the mounted checkout path if needed:

```powershell
python -m pytest tests/test_linux_launcher.py -q
wsl -d Ubuntu-TSC -- python3 /mnt/c/qhunliv13_web/_public_qqbot/tools/test_linux_launcher.py
```

The [standalone runner](../tools/test_linux_launcher.py) loads that same fixture;
there is no second Linux-only implementation to drift. Actual executable fixtures
are created on the Linux filesystem, not on the Windows-mounted checkout.
