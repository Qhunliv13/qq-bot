# Windows WSL / Docker 兼容安装与管理

> 本页保留原 Windows WSL 完整版指南。**根目录 `install.cmd` 现已默认进入原生完整版，不再进入本页流程。**以下旧文中的“双击 / 再次双击 install.cmd”均改为显式运行下面的 WSL 兼容入口；其余 WSL 参数、权限及数据边界保持不变。已有 WSL 实例请继续使用原目录与发行版，不自动迁移到原生目录。原生完整版见 [Windows](WINDOWS.md)。

```powershell
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File .\tools\windows-setup.ps1 -Graphical
```

`install.ps1`、`tools/windows.ps1` 和 `tools/windows-setup.ps1` 明确保留为 **WSL 兼容**入口，不是原生 Windows 安装命令。

**只想极轻量、不装 WSL / Docker？** 选择新增的 [`lite.cmd` 原生轻量版](WINDOWS_LITE.md)：Windows Python 3.10+ 标准库即可网页和命令行聊天，不含 QQ/工具/持久记忆。以下仍是完整运行时，不自动改成轻量模式。

## 最简单：完整解压后双击 install.cmd

1. 下载并**完整解压**仓库 ZIP，双击根目录 [`install.cmd`](../install.cmd)。入口以 `-STA -Graphical` 启动 Windows 原生欢迎窗口；即使没有 WSL 或 Python，也会先看到中文环境解释。不用先打开 PowerShell。
2. 确认开始检查 WSL2 和 Linux 发行版：只有一个可用发行版时自动使用，有多个时在图形下拉列表选择，不在终端输入编号；不会把 Docker Desktop 的内部发行版当作安装目标。
3. 检查 Linux Python 3.10+、Docker 和 Compose v2。缺依赖时先弹出明确的安装确认，经同意后才复用现有助手，从所选发行版配置好的签名 apt 软件源安装和启动 Docker；不替换或升级已有 Docker。安装可能持续几分钟，此阶段详细进度仍在启动窗口中，请不要关闭。
4. 环境就绪后自动打开 **Windows 默认浏览器中的本机安装网页**，而不是直接启动 Bot 或在终端询问模型。网页依次为 **准备好了吗 → 连接模型 → 创建 Bot → 启动与试聊 → 开始使用**：检查环境，填写模型地址/名称/Key，可选人格与 QQ 所有者，明确确认保存后，再单独确认启动、检查本地健康及可选试聊。API Key 只填在网页的密码框；可选 QQ 所有者可以留空。当前路径需要 OpenAI 兼容 HTTP 接口，不是任意供应商原生协议都可用。
5. 只有在网页确认后才构建并启动完整 Bot；以网页实际健康结果为准。试聊需要另行确认，可能产生模型费用并使用已授权的完整工具能力。最后进入完整控制台与 QQ 手动接入指南；填写所有者不等于 QQ 已登录或消息已验收。再次双击会保留原设置，跳过不可覆盖的模型/角色表单进入启动与试聊，不重置密码或记忆。关闭安装向导不代表部署成功，也不会停止已启动的 Bot；停止所选实例需要在网页中另行明确确认。

本机安装网页与 Bot 管理面板是两个不同入口。安装网页使用随机端口的回环地址和临时会话访问令牌；Windows 启动器只接受严格验证的 `127.0.0.1` 地址，并在内存中交给浏览器，不在控制台打印令牌。**不要分享安装网页链接、浏览器地址栏截图或密码。** 网页使用期间请保持启动窗口打开；关闭后该次网页可能不可再用，重新双击即可开启新会话。浏览器无法打开时，修复系统默认浏览器后关闭向导并重试。

没有 WSL 时，可授权向导调用微软 `wsl --install --distribution Ubuntu --no-launch`。硬件虚拟化、Windows 功能安装、系统重启和 Ubuntu 首次初始化，仍可能需要按系统提示完成；**不会强制重启、转换现有 WSL1 或停掉其它发行版**。完成后再次双击继续。

已有 Docker Desktop 请开启 **Use the WSL 2 based engine** 及所选发行版的 **Resources → WSL Integration**。向导自动依赖安装仅适用于提供 `docker.io` / `docker-compose-v2` 的 apt 发行版；包不存在、已有 Docker 缺匹配 Compose 插件、Desktop 未开启集成时会明确停止，不自动换源或替换 Docker。

首次构建需要镜像/依赖源网络及磁盘、内存；不会关闭 TLS、改 DNS 或默认代理来掩盖下载失败。本入口运行的是**完整本地运行时（full local runtime）与 AstrBot 容器、插件**，不是远程轻客户端；不自动安装 NapCat、登录 QQ 或迁移已有实例。

## 面板地址、账号与密码

默认本机面板：**http://127.0.0.1:6185**，用户名 **`astrbot`**。密码随机生成，私有数据存放在 **WSL Linux 中、仓库之外**，不是 ZIP 解压目录。

在仓库根目录打开 Windows PowerShell，运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 info
```

这会显示运行名、数据目录、私有配置路径、配置中的面板 URL 与用户名；**不是健康检查**，不需要 Docker、不启动服务、不联网、不调用模型。默认隐藏密码。只在自己的终端确实需要时运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 info -ShowPassword
```

显式选项只显示初始面板密码，不显示模型 API Key；已在面板修改的账号/密码以修改后为准，`info` 不重置它们。不要分享密码输出、截图或整个 `settings.json`；该文件还含模型秘密。PowerShell 的 `Bypass` 仅作用于本次进程，不修改系统执行策略。

**管理同一实例要用原参数。** 向导用所选 WSL 的 root 准备依赖和启动，但不改变默认 Linux 用户；默认数据路径仍取该默认用户的 `~/.local/share/qhunliv-bot`，不会自动新建第二套 root 默认配置。使用高级命令时沿用向导选择的发行版、`-User root` 和原 Linux 数据路径，例如（替换为实际值）：

```powershell
.\install.ps1 info -Distro Ubuntu -User root -DataDir '/home/your-user/.local/share/qhunliv-bot'
```

向导产生的 root-owned 数据可能需要 `-User root` 才能读取。不要为修复权限改成另一个新目录，也不要把 `/home/your-user/...` 示例当作真实路径。

## 常用命令

若系统已经允许脚本执行，可以直接用 `.\install.ps1`；否则用上面的完整 `powershell.exe ... -File` 前缀。以下示例默认使用原安装实例，必要时每条都加同样的 `-Distro`、`-User`、`-DataDir`、`-RuntimeName`。

```powershell
.\install.ps1 doctor       # 读取已初始化配置并检查环境，不构建/启动
.\install.ps1 status       # 查看容器状态，不验证模型或 QQ
.\install.ps1 health       # 检查本地入口，不调用模型或发送 QQ 消息
.\install.ps1 logs         # 跟踪最后 100 行，Ctrl+C 只退出日志
'Hello' | .\install.ps1 chat  # 主动请求模型，可能收费并执行获授权工具
.\install.ps1 stop         # 只停止归属确认的所选实例，不删除状态
.\install.ps1 up           # 使用原数据启动，可能构建镜像，不是升级/重置
```

`up` 默认等待本地运行时健康 300 秒；可用 `.\install.ps1 up -WaitTimeout 600`（整数 1–3600 秒）。这不是镜像构建总时限，也不验证模型 Key 或 QQ 登录。`chat` 的成功只证明该次请求，不证明 QQ 已接入或消息已送达。

| 问题 | 优先检查 |
|---|---|
| 提示未初始化、找不到配置 | 用首次安装的发行版/用户/目录运行；真正的新实例用 `up` 或 `init` |
| 面板打不开 | `info` 看配置地址，`status` / `logs` 看实际状态；自定义端口不要继续用 6185 |
| Docker 不可用 | 启动已有 Desktop 并开启所选 WSL 的 Integration，再跑 `doctor`；不删除实例 |
| 提示目录不归当前用户/安装 | 沿用向导打印的 `-User root -DataDir '实际Linux路径'`；不要删标记绕过 |
| 健康正常但模型请求失败 | 检查自己的模型服务/ID/权限；不要上传 Key 或日志原文 |
| 重传模型参数没有更新 | 初始化参数只负责首次设置；`init` / `up` 保留已有配置，见后文配置边界 |
| 无法输入或中文管道乱码 | 按下方输入说明操作；要求精确 Unicode 时优先在 Linux 终端请求 |

## 高级：手动准备后从命令行安装

命令行入口 [`install.ps1`](../install.ps1) 不安装 WSL/Python/Docker、不请求管理员权限、不修改系统配置；与双击向导的依赖准备阶段不同。它只把操作交给 WSL 中同一 [`tools/bootstrap.py`](../tools/bootstrap.py)，没有第二套安装、容器或状态管理核心。

先准备：

- WSL2 发行版与其中的 Python 3.10+，仓库可从 Linux 访问（通常在 `/mnt/c/...`）。
- 已运行的 Docker Desktop 及该发行版的 WSL Integration，或所选 Linux 中可访问的 Docker Engine / Compose v2。确认 `docker compose version` 与 `docker info` 可用。
- 自己的有效模型 Base URL、模型 ID 和 API Key；首次构建需要网络、时间与磁盘。

从仓库根目录执行，无参数就是 `up`，缺模型设置时交互询问：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

Windows PowerShell 5.1 与 PowerShell 7 均支持；从其它目录可用带引号的脚本绝对路径，包含空格的仓库路径可用。参数验证及默认命令由 [`tools/windows.ps1`](../tools/windows.ps1) 统一实现，该直接入口也保留。缺失/过旧 Linux Python、不可访问发行版会给出前置要求提示；Docker 检查仍由共享安装核心完成。

需要显式选择发行版、目录或运行名时，每次保持一致；不带 `-Distro` 使用 WSL 默认发行版。示例 URL 仅占位，不能连接真实模型：

```powershell
.\install.ps1 init -Distro Ubuntu -ModelBase 'https://provider.example/v1' -Model 'model-id'
.\install.ps1 doctor -Distro Ubuntu
.\install.ps1 up -Distro Ubuntu -WaitTimeout 600
.\install.ps1 info -Distro Ubuntu
```

`-OwnerQQ` 可提供一个或多个自己的 QQ ID（例如 `-OwnerQQ @('123456789','987654321')`）；这只是配置所有者，不启用适配器或登录。`-DataDir` / `-PersonaFile` 只接受**所选 WSL 内可访问的绝对 Linux 路径**，例如 `/home/your-user/bot-state` 与 `/home/your-user/persona.txt`，不接受 Windows 盘符、相对路径、引号/注入字符或 shell 片段。仓库路径由 `wslpath` 转换。

也可以手动打开同一本机浏览器向导，不自动准备依赖：

```powershell
.\install.ps1 wizard -Distro Ubuntu -User root -DataDir '/home/your-user/.local/share/qhunliv-bot'
```

该命令在前台保持运行，自动在 Windows 默认浏览器打开本机网页；不会立即启动 Bot。`wizard` 仍由同一个 Python 核心实现，Windows 只转交经过校验的地址。高级 CLI 的无参数 `up`、模型隐藏输入和其他原命令保持不变；只有双击入口默认改为图形向导。

默认私有目录在所选 Linux 用户家目录，建议不要放 `/mnt/c` 等 Windows 挂载盘；初始化使用 Linux 私有目录/文件权限并拒绝陌生非空目录。新实例可显式选新目录和运行名：

```powershell
.\install.ps1 init -Distro Ubuntu -DataDir '/home/your-user/bot-state' -PersonaFile '/home/your-user/persona.txt' -RuntimeName 'my-local-bot'
```

新安装在首次初始化时把端口保存到私有配置的 `deployment.web_port` / `deployment.onebot_port`，之后默认沿用；旧安装没有该字段时仍使用原默认值，不自动重写。首次选择非默认端口要在 **Linux** 初始化环境设置 `BOT_WEB_PORT` / `BOT_ONEBOT_PORT`，没有 Windows `-WebPort` 选项，Windows 环境变量也不自动传入 WSL。多实例步骤见 [Linux 说明](LINUX.md#多实例与端口)。显式 Linux 环境变量可临时覆盖已保存端口，实际监听变更还需 `up` 应用；单独 `info` 不改变运行状态。

## 可选：非交互 API Key

手动安装优先使用隐藏输入。`-ApiKeyEnv` 是**Linux 环境变量名，不是 Key 值**；Windows `$env:MY_MODEL_KEY` 不会自动注入 WSL。不要把密钥放进 Git、PowerShell 脚本、命令参数或共享 shell 初始化文件。

非交互安装需同时提供 `-ModelBase`、`-Model` 和在目标 WSL 进程中实际可用的 `-ApiKeyEnv` 变量：

```powershell
.\install.ps1 init -Distro Ubuntu -ModelBase 'https://provider.example/v1' -Model 'model-id' -ApiKeyEnv 'MY_MODEL_KEY' -NonInteractive
```

仅在另一个交互 Linux shell 中 `export` 不会传给新的 `wsl.exe --exec` 进程。可靠做法是在所选 WSL 的**同一 shell**隐藏读取、导出，再调用共享安装核心（示例路径须替换）：

```bash
cd /mnt/c/path/to/checkout
read -r -s -p 'Model API key: ' MY_MODEL_KEY; printf '\n'
export MY_MODEL_KEY
python3 tools/bootstrap.py up --model-base 'https://provider.example/v1' --model 'model-id' --api-key-env MY_MODEL_KEY --non-interactive
unset MY_MODEL_KEY
```

否则省略 `-NonInteractive`，由 Linux Python 的隐藏提示输入。反复 `init` / `up` 不替换已有密钥，也不重写初始人格和记忆；这些参数不是现有安装的配置覆盖接口。初始模型设置是生成配置的输入，已有 AstrBot 面板另有运行配置；不要假设手改 `settings.json` 就会更新当前模型。本轮没有模型配置热更新命令。

## 聊天输入与安全边界

`chat` 不带提示参数时读取标准输入（standard input）；Windows 入口不把消息文本放入 WSL 命令行参数。可输入一行，再在 Windows 控制台用 Ctrl+Z、Enter 表示 EOF（Linux 终端用 Ctrl+D），或使用管道：

```powershell
'Hello' | .\install.ps1 chat -Distro Ubuntu
```

Windows PowerShell 原生管道编码可能不同于 UTF-8，尤其非 ASCII 文本；精确中文/Unicode 输入优先在 Linux 终端请求。不要在聊天中放密钥，实际模型和运行时可能记录或传输请求。共享 Python 核心的容器客户端可能在**容器内 exec 参数**中传递提示词；Windows 入口不改变该既有行为。

## 只读预检与验证范围

不想安装或启动任何东西时，可先运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\windows-setup.ps1 -CheckOnly
```

它不安装包、不启动服务、不设置模型；即使同时传入 `-Graphical`，也不加载桌面界面、不弹窗、不询问安装。微软 WSL 操作说明可查 [Microsoft WSL commands](https://learn.microsoft.com/en-us/windows/wsl/basic-commands)。

离线测试用模拟 WSL 验证两个 PowerShell 入口的 argv 与管道 stdin；已有真实 WSL 只读检查验证过 `wslpath`、`--cd` 和无害 Python stdin 读取，不把这些结果当作镜像构建或模型请求验收。完整构建、本地健康、真实模型与 QQ 登录分别验收，见 [部署验证记录](QUICK_DEPLOY_VERIFICATION.md)。
