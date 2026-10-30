# Windows 原生完整版：部署与管理

> **实现 / 验收状态（2026-10-31）：**Windows Python 3.13 隔离实测已通过全部 63 项适用依赖与 `pip check`、真实完整宿主、面板 HTML / 初始密码登录及实际 Chromium 登录页渲染 / 登录、同一完整回路的本地合成模型回复、在途请求排空后进程退出、启动父进程退出后续跑及同状态重启。首次默认下载曾停滞，实测使用仓库外测试专用的官方摘要校验 / HTTPS 分段恢复，**不等于默认新机双击安装全流程已通过**。真实外部模型、QQ、面板登录之外的全页浏览器交互、整个终端窗口关闭 / 注销及外部工具桥接仍未验收；详情见 [部署验证记录](QUICK_DEPLOY_VERIFICATION.md)。

## 这是完整运行时，不是 Lite

- Windows 原生 Python **3.12 或 3.13**；新安装优先 3.12，已有私有 venv 沿用匹配的 Python 小版本。启动器不执行 `pyvenv.cfg` 中的任意路径；若原小版本基座缺失则停止并提示恢复，不自动重装或覆盖。无需 WSL、Docker、Node.js 或管理员权限，不修改系统执行策略，不通过管理员符号链接安装插件。
- Python 核心仍来自仓库内固定的 **AstrBot 4.28.1**，复制到私有 `runtime/astrbot` 后运行；插件以文件复制方式安装，保留同一模型 / 工具回路、记忆、排队、自主观察与持久任务，不另造 Lite 引擎。
- 完整核心依赖来自该版本 `pyproject.toml`，只装进私有 `runtime/.venv`，不做全局 pip 安装。依赖版本未完整锁定，不承诺逐字节可复现，也不保证每个外部能力已具备 Windows 依赖。
- Dashboard 只从官方 PyPI 的 **同版本 AstrBot wheel** 提取预构建前端，不用该 wheel 替换 Python 核心。下载须同时满足源码记录的 SHA256、HTTPS 版本元数据与实际文件摘要；不是作者签名或独立发布签名，不需要本机 Node 构建。
- [Lite](WINDOWS_LITE.md) 仍是单独的纯文字入口，不含完整工具、QQ、持久记忆与任务；两者目录和标记不共享。

## 首次启动

1. 完整解压仓库 ZIP，双击根目录 [install.cmd](../install.cmd)。它以 PowerShell `-STA` 启动 [windows-native.ps1](../tools/windows-native.ps1)，无参数默认 `wizard`；不是 `install.ps1`。
2. 若没有支持的 Python，只有在你明确同意后，启动器才可调用 `winget` 的官方 `Python.Python.3.12` 包做 **当前用户范围**安装。不请求提权，不安装 WSL / Docker；拒绝、非交互或缺少 winget 时停止并提示手动准备。不要为了测试安装器而在宿主执行此安装步骤。
3. 环境就绪后打开同一五步浏览器向导：**准备好了吗 → 连接模型 → 创建 Bot → 启动与试聊 → 开始使用**。API Key 只填网页密码框或安全隐藏输入；角色与 QQ 所有者可留空。当前模型路径要求 OpenAI 兼容 HTTP 接口。
4. 保存和启动分别确认。首次 `up` 会联网准备私有 venv、完整依赖、源码副本和固定前端；依赖下载可能较慢。`doctor` 的前置检查不能替代资产安装或本地就绪。
5. 本地健康成功后，试聊仍需明确确认费用 / 工具执行。真实模型成功与 QQ 登录 / 送达分别验收，不能从环境正常推出。

安装页使用随机 loopback 端口和临时令牌；它不是 Bot 控制台。授权链接只在本机浏览器传递，不应分享地址栏截图。保留安装窗口以使用该次向导；关闭窗口不会被当成停止命令。运行时以 detached 子进程启动的实现旨在允许关闭向导后继续运行，但该行为仍待真实 Windows smoke 验证。

## 数据、账号与端口

默认数据目录：**`%LOCALAPPDATA%\QhunlivBot\Full`**，在仓库之外。新目录使用当前 Windows 用户 SID 的私有 ACL，不提权，也不放宽已有陌生目录的权限；权限或归属不符时应停止，而不是删标记绕过。

| 相对数据目录的路径 | 用途 |
|---|---|
| `settings.json` | 初始模型秘密、角色、所有者、初始面板密码与保存端口；不要分享 |
| `.native-bootstrap.json` | 原生实例类型 / 运行名标记，不用于接管 Lite 或 WSL 状态 |
| `runtime/astrbot` | 固定 vendored 核心副本；其中 `data` 保存 AstrBot 状态和插件副本 |
| `runtime/.venv` | 此实例专用 Python 环境；不继承全局 site-packages |
| `runtime/pip-cache` | 此实例私有 pip 缓存；重试可复用已缓存下载 / 构建产物，不使用全局缓存，不保证部分文件续传 |
| `higan` | 同一插件的记忆、历史、任务和本地操作端点等私有状态 |
| `runtime/setup.log` | 资产安装固定步骤 / 退出状态；不保存 pip 原始输出 |
| `run/runtime.log` | 宿主私有运行日志，可能含聊天 / URL，**不得原样上传** |

面板只监听 **`127.0.0.1`**，默认端口 **6185**，初始用户名 `astrbot`、随机密码。首次隐式默认端口冲突会选择空闲端口并保存到 `deployment.web_port` / `deployment.onebot_port`；OneBot 初始候选端口为 6199，不代表已启用 QQ。后续沿用保存值，不停别人的服务。显式 `BOT_WEB_PORT` / `BOT_ONEBOT_PORT` 可用于首次选端口；已有状态不自动重分配。`info` 给出配置地址，不能证明监听成功。

更新前备份整个私有目录，备份也须保密。重复 `init` / `up` 不覆盖模型、密码、人格、记忆；已有 AstrBot 面板配置有自身状态，不承诺手改 `settings.json` 热更新。源码 / 前端副本不匹配时失败，不静默升级；无自动迁移或接管旧实例。

## PowerShell 管理命令

在仓库根目录运行；后续每条命令都沿用首次的 `-DataDir` 与 `-RuntimeName`。被执行策略阻止时，使用 `powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File .\tools\windows-native.ps1 info`；Bypass 仅作用于该进程。

```powershell
.\tools\windows-native.ps1                     # 默认 wizard，不直接启动
.\tools\windows-native.ps1 init                # 只初始化；缺输入时安全交互
.\tools\windows-native.ps1 up                  # 安装缺少的私有资产并启动，不自动升级
.\tools\windows-native.ps1 doctor              # 原生前置条件检查，不安装、不启动
.\tools\windows-native.ps1 info                # 配置地址和初始账号；默认隐藏密码
.\tools\windows-native.ps1 info -ShowPassword  # 仅自己的终端显式显示初始面板密码
.\tools\windows-native.ps1 info -Json          # 机器可读信息，不显示模型 Key
.\tools\windows-native.ps1 status              # 同一实例的认证本地健康
.\tools\windows-native.ps1 health              # 不调用模型、不发送 QQ
.\tools\windows-native.ps1 logs                # 仅打印私有日志路径，不输出 / 跟踪原始日志
'Hello' | .\tools\windows-native.ps1 chat       # stdin 输入，同一完整回路；可能收费 / 执行工具
.\tools\windows-native.ps1 stop                # 认证请求排空停止，保留数据
```

`stop` 的成功消息只表示同一实例接受关闭请求，不证明进程已退出；有在途请求时等待排空。当前实现先排空已接受本地请求及 QQ / 工具 worker，再交给上游核心取消和清理，不以按名称杀进程或强杀来模拟成功。关闭后生命周期仍待真实 smoke，不把模拟测试写成验收。

支持的参数：`-DataDir`（**绝对 Windows 路径**）、`-RuntimeName`、`-ModelBase`、`-Model`、`-ApiKeyEnv`、`-PersonaFile`（Windows 文件路径）、`-OwnerQQ`、`-NoBrowser`、`-Json`、`-ShowPassword`、`-WaitTimeout`、`-NonInteractive`。`-NoBrowser` 仅供 `wizard`；`-Json` / `-ShowPassword` 仅供 `info`。`-WaitTimeout` 为 1–3600 秒，默认 300，只限制启动后的就绪等待，不是依赖下载总时限。超时不自动杀实例。

```powershell
.\tools\windows-native.ps1 init -DataDir 'C:\BotData\MyFull' -RuntimeName 'my-full' -ModelBase 'https://provider.example/v1' -Model 'model-id' -ApiKeyEnv 'MY_MODEL_KEY' -NonInteractive
.\tools\windows-native.ps1 info -DataDir 'C:\BotData\MyFull' -RuntimeName 'my-full'
```

示例 URL / 模型只是占位。`-ApiKeyEnv` 是当前 Windows 进程中**环境变量名称**，绝不是 Key 值；应由受保护的会话 / 秘密管理工具提供。不要在命令历史、脚本、argv 或聊天中写密钥。`chat` 正文仅从 stdin 读取；Windows PowerShell 5.1 的非 ASCII 管道编码需单独核对，精确中文输入优先网页。`-OwnerQQ` 只配置授权所有者，不启用适配器或登录。

## 权限、完整能力与外部依赖

原生操作通道是 **loopback 端点 + 随机 operator token + 当前用户 SID 私有 ACL**，不是 Linux root-only Unix socket；端点定位与秘密文件不可分享或放宽权限。`health` / `chat` / `stop` 都针对该实例；本地请求复用完整插件、工具和数据，不伪造 QQ 消息。保护不隔离同一 Windows 用户下的恶意进程，也不宣称能抵抗系统管理员。

完整插件保留持久任务与 60% 原文 / 40% 摘要归档的可用聊天区分配，仍受模型、权限、执行锁和预算约束。保留代码 / 工具 schema **不代表全部外部能力一键可用**：

- QQ 为可选外部 OneBot / NapCat 服务或手动原生客户端配置；仓库不打包、不自动下载客户端、不自动 QQ 登录。原生 Windows 运行时不等于独立原生 QQ 后端已完成。
- VM、浏览器、邮件、指针 / 桌面桥接等仍需真实外部服务与授权；依赖 Linux 路径、命令或服务的桥接不会自动变成 Windows 宿主工具，也不自动获得宿主鼠标 / 文件权限。
- 面板、模型、QQ 登录、平台实际送达、外部工具与优雅停机分别验证。参见 [功能细节](FEATURES.md) 与 [部署验证记录](QUICK_DEPLOY_VERIFICATION.md)。

## 保留的其他入口

- **WSL / Docker 兼容**：[WINDOWS_WSL.md](WINDOWS_WSL.md)。`install.ps1`、`tools/windows.ps1`、`tools/windows-setup.ps1` 原样保留在各自职责中，不是原生安装入口；已有实例继续使用原 WSL 目录 / 用户 / 运行名。
- **Lite 纯文字版**：[WINDOWS_LITE.md](WINDOWS_LITE.md)，用 `lite.cmd`；不自动将完整安装降级为 Lite。
- **Linux 完整版**：[LINUX.md](LINUX.md)，仍走 Docker / Compose，不因 Windows 原生入口而改变。
