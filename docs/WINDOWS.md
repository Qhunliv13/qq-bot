# Windows 双击部署 / full local runtime

## 推荐：双击 install.cmd

完整解压仓库 ZIP 后，双击根目录 `install.cmd`。Windows Python、手动 PowerShell 命令都不需要。

1. 自动检测 WSL2；仅一个可用发行版时直接选用，多个时按编号选择，不接管 Docker Desktop 内部发行版。
2. 检查 Linux Python 3.10+、Docker 和 Compose v2。经你确认后，可从所选发行版已配置的签名 apt 软件源安装缺少的包并启动 Docker；已有 Docker 不替换、不升级。
3. 调用同一 `tools/bootstrap.py`。首次输入模型 URL、模型名、隐藏 API key 和可选 QQ 所有者；再次启动保留原设置。构建与健康检查成功后打开面板。

没有 WSL 时可授权向导调用微软 `wsl --install --distribution Ubuntu --no-launch`。硬件虚拟化、Windows 功能安装、重启、Ubuntu 首次初始化可能仍需你按系统提示完成；**不会强制重启、转换现有 WSL1 或停掉其它发行版**。完成后再次双击继续。

向导只在所选 WSL 中用 root 准备依赖和启动已有统一安装器，不改变默认 Linux 用户；数据仍使用该默认用户原有的 `~/.local/share/qhunliv-bot`，不会因切到 root 新建第二份默认配置。管理时可继续双击，也可使用下方高级命令；向导创建的 root-owned 状态应加 `-User root -DataDir '/home/你的用户/.local/share/qhunliv-bot'`（按向导打印的实际路径）。面板账号 `astrbot`，随机密码位于此目录的私有 `settings.json`；不要上传或分享。

自动依赖安装适用于提供 `docker.io` 和 `docker-compose-v2` 包的 apt 发行版；不存在这些包、已有 Docker 缺匹配 Compose 插件或 Desktop 未开启 WSL Integration 时明确停止，不自动切换软件源或替换 Docker。已有 Docker Desktop 请开启 **Resources → WSL Integration** 后重试。首次构建还需要官方镜像/依赖源网络、磁盘与内存；不会关闭 TLS、改 DNS 或默认代理来掩盖下载失败。

只读环境检查：`powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\windows-setup.ps1 -CheckOnly`。这不安装包、不启动服务、不配置模型。完整构建、模型真实请求与 QQ 登录的验收是不同阶段，见 [验证记录](QUICK_DEPLOY_VERIFICATION.md)。

官方 WSL 安装与重启说明：[Microsoft WSL commands](https://learn.microsoft.com/en-us/windows/wsl/basic-commands)。

## 高级命令行入口（WSL2 + Docker Desktop / Linux Engine）

This is the **full local** bot, including its image build and bundled dependencies; it is not a light-client or remote-service mode. The Windows script is only a launcher: all installation, Docker Compose, state safety, health and chat operations are implemented by the existing `tools/bootstrap.py` in Linux. No QQ/NapCat installation, login, or production migration is performed.

## Quick launch from the repository root

After the prerequisites below are ready, open ordinary Windows PowerShell (no administrator rights needed) in the checkout and run this **single entry point**:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

No arguments means `up`: missing model configuration is requested interactively, the API key prompt is hidden, then the shared Linux bootstrap checks/builds/starts the full local runtime. `Bypass` applies only to this PowerShell process; the launcher does not change execution-policy settings. It does not install WSL, Python or Docker, request administrator rights, or alter system configuration. First image build can download dependencies and take time/disk space. A quoted absolute script path works from another directory, including checkout paths containing spaces.

If script execution is already allowed, use ` .\install.ps1 ` directly. All existing options are forwarded unchanged, for example:

```powershell
.\install.ps1 -Distro Ubuntu -ModelBase 'https://provider.example/v1' -Model 'model-id'
.\install.ps1 status -Distro Ubuntu
```

The root script has no second parameter parser: validation and the default command live only in `tools/windows.ps1`. That direct entry remains supported too. Windows PowerShell 5.1 and PowerShell 7 are supported; missing/old Linux Python and inaccessible distributions produce prerequisite hints on stderr, while Docker/Compose checks remain in the shared bootstrap.

## Prerequisites

1. Install WSL2 with a Linux distribution, and install Python 3.10+ in that distribution. Keep this repository accessible from WSL (typically under `/mnt/c/...`).
2. Install and start Docker Desktop for Windows, enable **Use the WSL 2 based engine**, and enable **Resources → WSL Integration** for the selected distribution. In that distribution verify `docker compose version` and `docker info` work. The bootstrap `doctor` command performs its own Docker/Compose checks. This launcher does not install Docker or configure integration.
3. Run these commands in Windows PowerShell 5.1 or PowerShell 7 from the repository root. If using a named distribution, add `-Distro Ubuntu` to **each** command; otherwise the configured WSL default is used. Keep using the same distribution, `-DataDir`, and `-RuntimeName` for a given installation.

```powershell
.\install.ps1 init -Distro Ubuntu -ModelBase 'https://example.invalid/v1' -Model 'your-model' -OwnerQQ 123456789
.\install.ps1 doctor -Distro Ubuntu
.\install.ps1 up -Distro Ubuntu
.\install.ps1 status -Distro Ubuntu
.\install.ps1 health -Distro Ubuntu
.\install.ps1 logs -Distro Ubuntu
.\install.ps1 chat -Distro Ubuntu
```

`init` prompts for an API key in the **WSL Linux process** if a usable key has not been provided by its Linux environment. Supply a real OpenAI-compatible model base URL and model ID; the illustrative URL above does not connect to a provider. `up` initializes first if needed, then checks prerequisites and builds/starts the full local image (first build may take time and disk space). `logs` follows the last 100 log lines until interrupted. `health` checks the local runtime; `chat` sends one real model request. QQ login remains a separate manual step. Dashboard: `http://127.0.0.1:6185` after `up`; private credentials are stored in the Linux state directory's `settings.json`, never in this checkout.

### Startup readiness timeout

`up` waits for the local runtime health check, not for model authentication or QQ login. The shared Python bootstrap uses a **300-second** readiness timeout by default. Override it with `-WaitTimeout` (integer seconds, **1–3600**); the Windows launcher forwards `--wait-timeout` only when you supply the flag:

```powershell
.\install.ps1 up -Distro Ubuntu -WaitTimeout 600
```

A healthy container does not prove that your model API key is valid; `chat` makes a real model request separately. Port configuration remains the existing Linux `BOT_WEB_PORT` environment setting; there is no Windows `-WebPort` option, and Windows environment variables are not automatically transported into WSL.

### Linux API key environment (optional non-interactive setup)

`-ApiKeyEnv` is an **environment variable name**, not a key value. Set that variable in the **selected WSL distribution** before invoking `init`/first `up`, and verify it is available to non-interactive `wsl.exe --exec` processes. A Windows `$env:MY_MODEL_KEY` is **not automatically injected** into WSL. Do not put the secret on the command line, in Git, or in the PowerShell script. For non-interactive setup, all three of `-ModelBase`, `-Model`, and a populated Linux `-ApiKeyEnv` variable are required; with interactive setup the Python bootstrap securely prompts for missing fields. Avoid exporting the key in shared shell startup or tracked files; prefer a private Linux secret mechanism configured for the intended WSL process.

```powershell
.\install.ps1 init -Distro Ubuntu -ModelBase 'https://provider.example/v1' -Model 'model-id' -ApiKeyEnv 'MY_MODEL_KEY' -NonInteractive
```

A plain `export` in one interactive Linux shell does **not** make the variable available to a new Windows-launched `wsl.exe --exec` process. For reliable non-interactive bootstrap, open the chosen WSL distribution, read/export the secret there, and invoke the shared bootstrap **in that same Linux shell** (the read prompt is hidden and does not store the key in shell history):

```bash
cd /mnt/c/path/to/checkout
read -r -s -p 'Model API key: ' MY_MODEL_KEY; printf '\n'
export MY_MODEL_KEY
python3 tools/bootstrap.py up --model-base 'https://provider.example/v1' --model 'model-id' --api-key-env MY_MODEL_KEY --non-interactive
unset MY_MODEL_KEY
```

Otherwise omit `-NonInteractive` and let Python's hidden WSL prompt request the key. Windows environment-key forwarding is intentionally not implemented; do not pass a secret as `-ApiKeyEnv`. Existing initialized settings remain unchanged on repeated `init`/`up`; an absent key is not silently replaced.

### External paths and safe chat input

Default state is the selected Linux user's private `~/.local/share/qhunliv-bot`, outside the checkout; the bootstrap validates ownership and refuses unmanaged nonempty state. `-DataDir` and `-PersonaFile` accept **absolute Linux paths only** visible inside that WSL distribution (for example `/home/user/bot-state` and `/home/user/persona.txt`). Windows drive paths, relative paths, quoted/injection characters and shell fragments are rejected. Prefer a Linux home path for secret state, rather than storing it on a Windows drive under `/mnt/c`; Python creates owner-private files and directories in Linux. The checkout is translated with `wslpath`; the launcher never creates a second Python bootstrap or passes a model API key in argv.

```powershell
.\install.ps1 init -Distro Ubuntu -DataDir '/home/user/bot-state' -PersonaFile '/home/user/persona.txt' -RuntimeName 'my-local-bot' -OwnerQQ @('123456789','987654321')
'Hello' | .\install.ps1 chat -Distro Ubuntu
```

For interactive chat, enter one line directly into WSL's standard input and signal EOF (`Ctrl+Z` then Enter in Windows PowerShell; `Ctrl+D` in a Linux console), or pipe a file/stream to the script. **No chat message appears in the WSL command-line arguments.** Offline tests execute Windows PowerShell with a fake WSL command to verify argv and pipeline stdin through both entry points. Read-only checks on Ubuntu-TSC (WSL2, Python 3.12.3) verified `wslpath`, `--cd`, and piped stdin to a harmless Python reader; Docker was absent, so no runtime build or chat/model request was attempted. Windows PowerShell's native pipeline encoding can differ from UTF-8 (especially for non-ASCII input); prefer interactive chat in the Linux terminal when exact Unicode input matters. Do not place secrets in chat text; the actual bot/model may log or transmit the request. The Python bootstrap's internal container client may pass its prompt inside the container exec argv; the Windows launcher does not alter that existing behavior.
