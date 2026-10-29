# Windows full local runtime (WSL2 + Docker Desktop)

This is the **full local** bot, including its image build and bundled dependencies; it is not a light-client or remote-service mode. The Windows script is only a launcher: all installation, Docker Compose, state safety, health and chat operations are implemented by the existing `tools/bootstrap.py` in Linux. No QQ/NapCat installation, login, or production migration is performed.

## Prerequisites

1. Install WSL2 with a Linux distribution, and install Python 3.12+ in that distribution. Keep this repository accessible from WSL (typically under `/mnt/c/...`).
2. Install and start Docker Desktop for Windows, enable **Use the WSL 2 based engine**, and enable **Resources → WSL Integration** for the selected distribution. In that distribution verify `docker compose version` and `docker info` work. The bootstrap `doctor` command performs its own Docker/Compose checks. This launcher does not install Docker or configure integration.
3. Run these commands in Windows PowerShell 5.1 or PowerShell 7 from the repository root. If using a named distribution, add `-Distro Ubuntu` to **each** command; otherwise the configured WSL default is used. Keep using the same distribution, `-DataDir`, and `-RuntimeName` for a given installation.

```powershell
.\tools\windows.ps1 init -Distro Ubuntu -ModelBase 'https://example.invalid/v1' -Model 'your-model' -OwnerQQ 123456789
.\tools\windows.ps1 doctor -Distro Ubuntu
.\tools\windows.ps1 up -Distro Ubuntu
.\tools\windows.ps1 status -Distro Ubuntu
.\tools\windows.ps1 health -Distro Ubuntu
.\tools\windows.ps1 logs -Distro Ubuntu
.\tools\windows.ps1 chat -Distro Ubuntu
```

`init` prompts for an API key in the **WSL Linux process** if a usable key has not been provided by its Linux environment. Supply a real OpenAI-compatible model base URL and model ID; the illustrative URL above does not connect to a provider. `up` initializes first if needed, then checks prerequisites and builds/starts the full local image (first build may take time and disk space). `logs` follows the last 100 log lines until interrupted. `health` checks the local runtime; `chat` sends one real model request. QQ login remains a separate manual step. Dashboard: `http://127.0.0.1:6185` after `up`; private credentials are stored in the Linux state directory's `settings.json`, never in this checkout.

### Linux API key environment (optional non-interactive setup)

`-ApiKeyEnv` is an **environment variable name**, not a key value. Set that variable in the **selected WSL distribution** before invoking `init`/first `up`, and verify it is available to non-interactive `wsl.exe --exec` processes. A Windows `$env:MY_MODEL_KEY` is **not automatically injected** into WSL. Do not put the secret on the command line, in Git, or in the PowerShell script. For non-interactive setup, all three of `-ModelBase`, `-Model`, and a populated Linux `-ApiKeyEnv` variable are required; with interactive setup the Python bootstrap securely prompts for missing fields. Avoid exporting the key in shared shell startup or tracked files; prefer a private Linux secret mechanism configured for the intended WSL process.

```powershell
.\tools\windows.ps1 init -Distro Ubuntu -ModelBase 'https://provider.example/v1' -Model 'model-id' -ApiKeyEnv 'MY_MODEL_KEY' -NonInteractive
```

If your Linux environment does not retain the variable when launched by Windows, omit `-NonInteractive` and let Python's hidden WSL prompt request the key. Existing initialized settings remain unchanged on repeated `init`/`up`; an absent key is not silently replaced.

### External paths and safe chat input

Default state is the selected Linux user's private `~/.local/share/qhunliv-bot`, outside the checkout; the bootstrap validates ownership and refuses unmanaged nonempty state. `-DataDir` and `-PersonaFile` accept **absolute Linux paths only** visible inside that WSL distribution (for example `/home/user/bot-state` and `/home/user/persona.txt`). Windows drive paths, relative paths, quoted/injection characters and shell fragments are rejected. Prefer a Linux home path for secret state, rather than storing it on a Windows drive under `/mnt/c`; Python creates owner-private files and directories in Linux. The checkout is translated with `wslpath`; the launcher never creates a second Python bootstrap or passes a model API key in argv.

```powershell
.\tools\windows.ps1 init -Distro Ubuntu -DataDir '/home/user/bot-state' -PersonaFile '/home/user/persona.txt' -RuntimeName 'my-local-bot' -OwnerQQ @('123456789','987654321')
'Hello' | .\tools\windows.ps1 chat -Distro Ubuntu
```

For interactive chat, enter one line directly into WSL's standard input and signal EOF (`Ctrl+Z` then Enter in Windows PowerShell; `Ctrl+D` in a Linux console), or pipe a file/stream to the script. **No chat message appears in the WSL command-line arguments.** Offline tests verify argv shape only; chat stdin with an actual WSL executable has not yet been exercised on a configured host. Do not place secrets in chat text; the actual bot/model may log or transmit the request. The Python bootstrap's internal container client may pass its prompt inside the container exec argv; the Windows launcher does not alter that existing behavior.
