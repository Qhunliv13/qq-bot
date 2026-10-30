# 彼岸酱（Qhunliv-bot）

开源、可自托管的聊天与工具助手，内嵌固定版本 AstrBot。用自己的模型 API，在本地面板或命令行使用；也可另外连接 QQ。包含持久记忆、工具调用、排队执行与后台任务，无需使用作者的账号、模型供应商或服务器。

## 零基础开始（完整版，启动即有界面）

**Windows：**下载并完整解压仓库 ZIP，双击根目录 [`install.cmd`](install.cmd)，默认进入**原生完整版**浏览器向导，**无需 WSL / Docker / Node，不是 Lite**。需要 Windows Python 3.12 / 3.13；缺少时仅在明确同意后可通过 winget 安装官方 Python 3.12 到当前用户，不提权。完整依赖只安装到仓库外私有 venv。**Python 3.12 在全新私有目录中的默认完整安装已通过，无预置缓存或下载补丁；完整宿主、面板登录、本地合成模型回复及排空停止 / 重启也已实测。仍不等于全新电脑双击、真实模型或 QQ 全流程已验收**，详见 [Windows 说明](docs/WINDOWS.md)。

浏览器按 **准备好了吗 → 连接模型 → 创建 Bot → 启动与试聊 → 开始使用** 引导。模型填三项；角色和 QQ 管理员可跳过。点击启动后可直接试聊，再选择网页使用或按指南连接 QQ。完整工具、记忆和任务保留，不是 Lite。重复打开进入同一 Bot 管理页，不覆盖原配置。[图文式零基础操作说明](docs/VISUAL_SETUP.md)

**Linux：**准备 Python 3.10+、Docker Engine 与 Compose v2，在仓库根目录运行：

```bash
bash ./install.sh
```

首次准备需网络、时间和磁盘；Linux 入口不自动安装系统依赖。Linux 完整版仍用 Docker；已有 Windows WSL 实例可显式使用保留的 [WSL / Docker 兼容入口](docs/WINDOWS_WSL.md)，不自动迁移。[原生 Windows 详细部署](docs/WINDOWS_NATIVE_FULL.md) · [Linux 说明](docs/LINUX.md)

## 可选：Windows 原生极轻量版

只想文字聊天、不需要工具或 QQ 时，可选择 **不需要 WSL / Docker / pip** 的 [原生轻量版](docs/WINDOWS_LITE.md)：安装 Windows Python 3.10+ 后，双击 [lite.cmd](lite.cmd)。密钥由当前用户 DPAPI 加密；**不含 QQ、工具、持久记忆与后台任务**，不替代完整版，也不会被完整向导偷偷选中。

## 模型设置：只需三项

| 提示项 | 填写内容 |
|---|---|
| 模型地址 / Base URL | 你的服务提供的 HTTP(S) API 地址，是否带 `/v1` 按服务说明 |
| 模型名称 / Model ID | 服务实际支持的模型 ID，不是任意显示名 |
| API Key | 自己的有效凭据；网页密码框输入，不写进命令行、网址或 Git |

当前接入使用 **OpenAI 兼容 HTTP 接口**，不宣称支持所有供应商原生协议。可选 QQ 所有者可以留空；默认没有预置个人身份或强制人设。

启动后，在向导第五步点击“打开网页聊天”或“打开完整控制台”。初始用户名为 **`astrbot`**；点击“查看初始登录信息”并确认后可临时显示密码，30 秒后自动隐藏。也可用下方高级 `info` 命令查看地址；默认面板端口 6185，首次冲突时选用空闲端口。已在面板改过的密码不会重置；不要分享密码、授权链接、截图或私有配置。

## 常用操作

在仓库根目录操作，沿用首次安装的数据目录、运行名与端口。下表 Windows 指**原生完整版**，自定义安装每条均沿用同一 `-DataDir`（绝对 Windows 路径）和 `-RuntimeName`。旧 `install.ps1` 仅管理 [WSL 兼容实例](docs/WINDOWS_WSL.md)。

| 操作 | Linux | Windows PowerShell |
|---|---|---|
| 地址、账号与数据位置 | `bash ./install.sh info` | `.\tools\windows-native.ps1 info` |
| 环境预检 | `bash ./install.sh doctor` | `.\tools\windows-native.ps1 doctor` |
| 实例状态 | `bash ./install.sh status` | `.\tools\windows-native.ps1 status` |
| 本地健康检查 | `bash ./install.sh health` | `.\tools\windows-native.ps1 health` |
| 日志入口 | `bash ./install.sh logs` | `.\tools\windows-native.ps1 logs` |
| 本地请求一次模型 | `bash ./install.sh chat --prompt '你好'` | `'Hello' \| .\tools\windows-native.ps1 chat` |
| 停止此实例（保留数据） | `bash ./install.sh stop` | `.\tools\windows-native.ps1 stop` |
| 使用原数据启动 | `bash ./install.sh up` | `.\tools\windows-native.ps1 up` |

PowerShell 若阻止脚本，用 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\windows-native.ps1 info`（其它命令替换 `info`）；仅本次进程放行，不改系统策略。

- `info` 不需要 Docker、不联网，默认隐藏密码；显式显示的也只有面板密码，不显示模型 API Key。配置中的 URL 不是就绪证明。
- `health` 不调用模型、不发 QQ 消息；`chat` 请求同一完整回路，可能收费并执行获授权工具。原生 `logs` 只显示私有日志路径，不输出原始日志；Linux 的 Ctrl+C 仅退出日志跟踪。
- 原生 Windows 私有数据默认在 `%LOCALAPPDATA%\QhunlivBot\Full`，新目录限当前用户 SID ACL；Linux / WSL 仍在各自 Linux 私有目录。`stop` 保留数据；原生停止回复只表示接受排空请求，不证明已退出。原生 `up` 不自动升级；Linux `up` 可能构建镜像，均不是迁移 / 配置覆盖命令。
- 初始模型设置用于生成配置；已有 AstrBot 面板另有运行配置，不保证改初始化文件就更新当前模型。遇到问题先读[快速开始与排障](docs/QUICKSTART.md)。

## 全系统架构与状态

[详细架构、所有模块状态、运行逻辑与存储说明](docs/SYSTEM_ARCHITECTURE_STATUS_20261030.md)包含完整/轻量入口区别、消息和模型链路、长度预算与有效摘要、唤醒人落盘、任务与自主观察、QQ/VM依赖、权限差异和未完成能力；不把源码实现当作线上已部署。

## 能做什么，有哪些边界

| 能力 | 当前边界 |
|---|---|
| 本地面板、命令行对话 | 默认无需 QQ；模型、健康与 QQ 登录分别验证 |
| 群聊共享记忆、历史检索、带来源事实 | 私聊不并入共享群时间线；跨群内容共享需先确认使用范围；不是向量检索 |
| 记忆、文件、任务等原生工具 | 工具目录存在不等于可用，依赖模型、权限与外部服务；虚拟机/邮件等需另配服务 |
| 排队、主动观察、后台任务、只读委派 | 受预算、执行锁与服务可用性约束，不保证准点交付或无限执行 |
| 可选 QQ / OneBot 接入 | 不打包 NapCat/QQ，不自动登录；实际发送需平台回执，健康不等于送达 |
| 完整运行时与可选 Lite 文字入口 | Windows 原生完整版保留同一核心、工具、记忆、任务与 60/40 聊天区预算；Linux / WSL 完整版仍用 Docker。Lite 不含完整面板、QQ、工具、持久记忆或任务 |

完整功能说明与已知限制见 [功能细节](docs/FEATURES.md)。Windows 原生不等于独立原生 QQ 后端完成：QQ / NapCat 仍须外部服务或客户端手动配置，不自动登录。VM、浏览器、邮件与指针桥接的 Linux 依赖不会自动变成 Windows 宿主工具，不能宣称全部外部能力一键可用。实际构建、模型请求与 QQ 登录的验收范围见 [部署验证记录](docs/QUICK_DEPLOY_VERIFICATION.md)，不把安装入口测试当作完整部署成功。

## 文档

- [系统总览](docs/SYSTEM_ARCHITECTURE_STATUS_20261030.md)：整个系统的架构、请求链路、模块职责、当前状态与已知缺口。
- [快速开始](docs/QUICKSTART.md)：从安装到第一次请求、密码查找、常见问题与可选 QQ。
- [Windows](docs/WINDOWS.md) / [Linux](docs/LINUX.md)：平台步骤、隔离目录、权限与端口。
- [配置与数据边界](docs/CONFIGURATION.md)：外部配置、账号授权与已有实例兼容。
- [功能细节](docs/FEATURES.md)：记忆、工具、任务、输出边界及源码目录，原技术说明保留。
- [全部文档](docs/README.md)：高级用法、架构、研究与证据；[工具索引](tools/README.md)区分检查和有副作用的探针。

## 开源与归属

本项目采用 **AGPL-3.0-only**，见 [LICENSE](LICENSE)。内嵌 AstrBot 的原始版权、许可和声明完整保留，见 [第三方声明](THIRD_PARTY_NOTICES.md)；NapCat、QQ 客户端、登录会话和凭据不在仓库中。

当前版本以检出的 Git 提交与 tag 为准；[历史档案](trash/README.md)保留旧记录供追溯，不是当前操作入口或部署授权。不要提交密钥、运行数据库、聊天档案、附件或私有配置。
