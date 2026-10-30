# 彼岸酱（Qhunliv-bot）

开源、可自托管的聊天与工具助手，内嵌固定版本 AstrBot。用自己的模型 API，在本地面板或命令行使用；也可另外连接 QQ。包含持久记忆、工具调用、排队执行与后台任务，无需使用作者的账号、模型供应商或服务器。

## 开始使用

**Windows：**下载并完整解压仓库 ZIP，双击根目录 [`install.cmd`](install.cmd)。不用先装 Windows Python；向导检查 WSL2 与 Docker，缺依赖时先征求同意。Windows 提示重启或首次初始化 Ubuntu 时，完成后再次双击继续，不会强制重启。

**Linux：**准备 Python 3.10+、Docker Engine 与 Compose v2，在仓库根目录运行：

```bash
bash ./install.sh
```

首次构建需网络、时间和磁盘；Linux 入口不自动安装系统依赖。已有 Docker 不替换；Windows Docker Desktop 需开启所选 WSL 发行版的 Integration。[Windows 说明](docs/WINDOWS.md) · [Linux 说明](docs/LINUX.md)

## 模型设置：只需三项

| 提示项 | 填写内容 |
|---|---|
| 模型地址 / Base URL | 你的服务提供的 HTTP(S) API 地址，是否带 `/v1` 按服务说明 |
| 模型名称 / Model ID | 服务实际支持的模型 ID，不是任意显示名 |
| API Key | 自己的有效凭据；安装时隐藏输入，不写进命令行或 Git |

当前接入使用 **OpenAI 兼容 HTTP 接口**，不宣称支持所有供应商原生协议。可选 QQ 所有者可以留空；默认没有预置个人身份或强制人设。

启动后打开 **http://127.0.0.1:6185**，面板用户名 **`astrbot`**。用下面的 `info` 查看配置地址和私有数据目录；需要密码时，Linux 加 `--show-password`、Windows 加 `-ShowPassword`。不要分享密码输出、截图或整个 `settings.json`。

## 常用操作

在仓库根目录操作，沿用首次安装的发行版、用户、数据目录、运行名与端口。Windows 向导若打印了带 `-Distro`、`-User`、`-DataDir` 的命令，请使用实际参数。

| 操作 | Linux | Windows PowerShell |
|---|---|---|
| 地址、账号与数据位置 | `bash ./install.sh info` | `.\install.ps1 info` |
| 环境预检 | `bash ./install.sh doctor` | `.\install.ps1 doctor` |
| 容器状态 | `bash ./install.sh status` | `.\install.ps1 status` |
| 本地健康检查 | `bash ./install.sh health` | `.\install.ps1 health` |
| 跟踪日志 | `bash ./install.sh logs` | `.\install.ps1 logs` |
| 本地请求一次模型 | `bash ./install.sh chat --prompt '你好'` | `'Hello' \| .\install.ps1 chat` |
| 停止此实例（保留数据） | `bash ./install.sh stop` | `.\install.ps1 stop` |
| 使用原数据启动 | `bash ./install.sh up` | `.\install.ps1 up` |

PowerShell 若阻止脚本，用 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 info`（其它命令替换 `info`）；仅本次进程放行，不改系统策略。

- `info` 不需要 Docker、不联网，默认隐藏密码；显式显示的也只有面板密码，不显示模型 API Key。配置中的 URL 不是就绪证明。
- `health` 不调用模型、不发 QQ 消息；`chat` 会请求你的模型，可能收费并按请求执行获授权工具。Ctrl+C 退出日志查看不停止 bot。
- 私有数据在仓库外（Windows 在 WSL 中）；`stop` 不擦除数据。重复安装保留配置，`up` 可能构建镜像，不是升级、迁移或模型配置覆盖命令。
- 初始模型设置用于生成配置；已有 AstrBot 面板另有运行配置，不保证改初始化文件就更新当前模型。遇到问题先读[快速开始与排障](docs/QUICKSTART.md)。

## 能做什么，有哪些边界

| 能力 | 当前边界 |
|---|---|
| 本地面板、命令行对话 | 默认无需 QQ；模型、健康与 QQ 登录分别验证 |
| 群聊共享记忆、历史检索、带来源事实 | 私聊不并入共享群时间线；跨群内容共享需先确认使用范围；不是向量检索 |
| 记忆、文件、任务等原生工具 | 工具目录存在不等于可用，依赖模型、权限与外部服务；虚拟机/邮件等需另配服务 |
| 排队、主动观察、后台任务、只读委派 | 受预算、执行锁与服务可用性约束，不保证准点交付或无限执行 |
| 可选 QQ / OneBot 接入 | 不打包 NapCat/QQ，不自动登录；实际发送需平台回执，健康不等于送达 |
| 完整运行时与可选 Lite 文字入口 | 完整版依赖 Docker；Lite 不含面板、QQ、工具、持久记忆或后台任务 |

完整功能说明与已知限制见 [功能细节](docs/FEATURES.md)。独立原生 QQ 后端仍未完成，不能据静态清单或离线测试替换现有接入。实际构建、模型请求与 QQ 登录的验收范围见 [部署验证记录](docs/QUICK_DEPLOY_VERIFICATION.md)，不把安装入口测试当作完整部署成功。

## 文档

- [快速开始](docs/QUICKSTART.md)：从安装到第一次请求、密码查找、常见问题与可选 QQ。
- [Windows](docs/WINDOWS.md) / [Linux](docs/LINUX.md)：平台步骤、隔离目录、权限与端口。
- [配置与数据边界](docs/CONFIGURATION.md)：外部配置、账号授权与已有实例兼容。
- [功能细节](docs/FEATURES.md)：记忆、工具、任务、输出边界及源码目录，原技术说明保留。
- [全部文档](docs/README.md)：高级用法、架构、研究与证据；[工具索引](tools/README.md)区分检查和有副作用的探针。

## 开源与归属

本项目采用 **AGPL-3.0-only**，见 [LICENSE](LICENSE)。内嵌 AstrBot 的原始版权、许可和声明完整保留，见 [第三方声明](THIRD_PARTY_NOTICES.md)；NapCat、QQ 客户端、登录会话和凭据不在仓库中。

当前版本以检出的 Git 提交与 tag 为准；[历史档案](trash/README.md)保留旧记录供追溯，不是当前操作入口或部署授权。不要提交密钥、运行数据库、聊天档案、附件或私有配置。
