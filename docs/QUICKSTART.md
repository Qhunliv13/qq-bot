# 快速开始

在自己的电脑或 Linux 服务器上运行彼岸酱（Qhunliv-bot）：安装、填写自己的模型设置，再从本地面板或命令行使用。**不需要先连接 QQ**；QQ 是可选接入，不随安装自动登录。

## 零基础优先：使用图形向导

Windows 双击根目录 [install.cmd](../install.cmd)，默认启动**原生完整版**，环境就绪后打开浏览器；无需 WSL / Docker / Node。需 Python 3.12 / 3.13，缺少时仅在明确同意后可用 winget 安装到当前用户，不提权。Linux `bash ./install.sh` 默认也打开浏览器向导。按 **准备好了吗 → 连接模型 → 创建 Bot → 启动与试聊 → 开始使用** 完成，具体按钮说明见 [零基础操作指南](VISUAL_SETUP.md)。

模型在网页密码框中填写，角色和 QQ 管理员可跳过；启动后可以直接进行一次明确确认费用的真实试聊。顶部把环境、本机 Bot、模型回复与 QQ 连接分别标识，不会混为“部署成功”。第五步提供网页聊天、完整控制台与初始登录信息按钮，无需手敲查询命令。重复打开进入原 Bot 管理页，保留配置。

下方 Windows CLI 指 `tools/windows-native.ps1`，无参数默认 `wizard`；显式 `up` 仍可交互初始化。旧 `install.ps1`、`tools/windows.ps1` / `windows-setup.ps1` 明确保留为 [WSL 兼容](WINDOWS_WSL.md)，不是原生入口。**Python 3.13 隔离实测已通过完整宿主、面板登录、本地合成模型回复及排空停止 / 重启；首次默认下载仍有停滞验收缺口，真实模型与 QQ 未验收**，见 [验证记录](QUICK_DEPLOY_VERIFICATION.md)。无桌面服务器与端口转发见 [Linux](LINUX.md)。

## 1. 启动安装

先下载并**完整解压**仓库 ZIP，或克隆完整仓库；不要只下载单个启动脚本。

| 平台 | 在仓库根目录操作 | 需要知道 |
|---|---|---|
| Windows | 双击 [`install.cmd`](../install.cmd) | 原生 Python 3.12 / 3.13；缺少时先征求当前用户安装同意，无需 WSL / Docker / 管理员 |
| Linux | 运行 `bash ./install.sh` | 预先准备 Python 3.10+、可访问的 Docker Engine 与支持 `up --wait` 的 Compose v2；脚本不自动安装系统依赖 |

首次准备需要网络、磁盘、内存和时间，不是离线安装。Windows 将 vendored AstrBot **4.28.1** 复制到私有 `runtime/astrbot`，插件复制而非管理员 symlink；Python 完整依赖取同版 `pyproject.toml`，仅装私有 venv。Dashboard 使用官方 PyPI **同版本、SHA256 固定校验**的预构建前端，不需 Node；不是作者签名。Linux / WSL 仍构建容器。Python 依赖未完整锁定，不承诺逐字节可复现。

Windows 原生安装不会准备 WSL / Docker，也不自动迁移已有实例；旧实例继续按 [WSL 指南](WINDOWS_WSL.md)管理。详见 [Windows](WINDOWS.md) / [原生部署细节](WINDOWS_NATIVE_FULL.md) / [Linux](LINUX.md)。

## 2. 填写三项模型设置

| 提示项 | 填什么 | 注意 |
|---|---|---|
| 模型地址（Base URL） | 你的模型服务给出的 HTTP(S) API 地址 | 按服务说明决定是否带 `/v1`；不要把完整聊天请求路径当作 Base URL |
| 模型名称（Model ID） | 该服务实际提供的模型 ID | 不是随意填写的显示名 |
| API Key | 你自己的有效模型凭据 | 安装时隐藏输入；不要写进命令行、聊天、Git 或截图 |

填错地址、模型名或 QQ 号时，交互向导只重问当前项目，不清空已经填对的内容；API Key 始终隐藏输入，不支持安全隐藏的终端会明确停止。传命令行参数的自动化模式仍直接报告非法输入，不悄悄改值。

当前安装路径使用 **OpenAI 兼容的 HTTP 接口**，不是任意供应商原生协议的通用适配器；具体模型与工具调用能力仍需验证。可选 QQ 所有者 ID 可以留空，不填写不会授权任何 QQ 账号，也不会启用 QQ 登录。初始人格默认为空文本，需要时在 Linux 初始化用 `init --persona-file`，Windows 原生用 `init -PersonaFile` 提供原文文件。

私有数据在仓库之外：Windows 原生默认 **`%LOCALAPPDATA%\QhunlivBot\Full`**，新目录限当前用户 SID ACL；Linux / WSL 默认 `~/.local/share/qhunliv-bot`。后续沿用相同数据目录与运行名，不共享 / 接管 Lite 或 WSL 状态。首次选端口会保存，原生详见 [部署细节](WINDOWS_NATIVE_FULL.md)，Linux / WSL 见 [端口说明](LINUX.md#多实例与端口)。

## 3. 打开面板

启动成功后打开安装器打印的本机地址；默认 **http://127.0.0.1:6185**，首次默认端口被占用时自动选择空闲端口并保存，不会停止已有服务。Windows 双击向导会按保存的地址尝试自动打开。面板用户名为 **`astrbot`**，密码随机生成。

| 查什么 | Linux | Windows PowerShell（仓库根目录） |
|---|---|---|
| 地址、用户名、数据目录与配置路径 | `bash ./install.sh info` | `.\tools\windows-native.ps1 info` |
| **仅在自己的终端**显示面板密码 | `bash ./install.sh info --show-password` | `.\tools\windows-native.ps1 info -ShowPassword` |

`info` 不需要 Docker，不启动服务、不访问网络，也不调用模型；默认隐藏密码，显式显示的也只有面板密码，**不显示模型 API Key**。它显示的是配置中的地址，**不是运行就绪证明**。这里显示的是私有 `settings.json` 中的初始用户名/密码；如果你已在面板中改过登录信息，初始值可能不再有效，`info` 不重置密码。不要分享整个文件、密码输出或终端截图。

若 PowerShell 执行策略阻止直接运行脚本，可用 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\windows-native.ps1 info`；其它命令同理。此 Bypass 只作用于本次进程，不修改系统执行策略。

## 4. 日常使用与管理

以下默认管理初次安装的同一实例。原生自定义安装每条命令沿用 `-DataDir`（Windows 绝对路径）和 `-RuntimeName`；WSL 的 `-Distro` / `-User` 不用于原生入口。

| 目的 | Linux | Windows PowerShell | 副作用与判断 |
|---|---|---|---|
| 环境预检 | `bash ./install.sh doctor` | `.\tools\windows-native.ps1 doctor` | Windows 检查原生前置条件；Linux 检查 Docker。均不安装或启动 |
| 查看实例状态 | `bash ./install.sh status` | `.\tools\windows-native.ps1 status` | 不验证模型凭据或 QQ 登录 |
| 本地入口健康 | `bash ./install.sh health` | `.\tools\windows-native.ps1 health` | 不调用模型、不发送 QQ 消息 |
| 日志入口 | `bash ./install.sh logs` | `.\tools\windows-native.ps1 logs` | 原生只给私有日志路径；Linux 跟踪日志，Ctrl+C 不停 Bot。均不得上传原文 |
| 主动发起一次本地请求 | `bash ./install.sh chat --prompt '你好'` | `'Hello' \| .\tools\windows-native.ps1 chat` | 会调用自己的模型，可能产生费用，并可能按请求执行工具 |
| 停止此实例 | `bash ./install.sh stop` | `.\tools\windows-native.ps1 stop` | 保留数据；原生认证排空请求 accepted 不等于进程已退出 |
| 启动或恢复运行 | `bash ./install.sh up` | `.\tools\windows-native.ps1 up` | 复用原数据；原生不自动升级，Linux 可能构建镜像；不重置 |

`chat` 不另建一套模型循环，调用的是同一实例的完整本地入口；也可不带 `--prompt`，从标准输入读取。Linux 本地 socket 使用 root peer credentials；Windows 原生是 loopback 端点 + operator token + 当前用户 SID 私有 ACL，复用同一完整插件回路，不隔离同用户恶意进程。结果进入原历史，不伪造 QQ 送达。模型成功、服务健康、QQ 已登录和 QQ 消息已送达是四种不同结果。

## 遇到问题先看这里

| 现象 | 先做什么 | 不要做什么 |
|---|---|---|
| 不记得地址或面板密码 | 对同一实例运行 `info`；需要密码时显式加显示选项 | 不上传 `settings.json`，不重新初始化来找密码 |
| 提示还未初始化 | 用原入口运行 `up` / `init`；核对原生 Windows 目录，或 WSL 的原发行版 / 目录 | 不把另一个实例的私有目录当作新目录 |
| 原生 Python / 资产未就绪 | 用 Python 3.12 / 3.13 运行 `doctor`；确认网络与私有磁盘，首次 `up` 准备依赖 | 不做全局 pip 安装、不关闭 TLS、不删除已有数据 |
| Linux / WSL Docker 不可用 | 检查 Docker / Compose；WSL 检查 Desktop Integration | 不换未知镜像源、不擅自替换已有 Docker |
| 启动超时或面板打不开 | 核对 `info` 的配置地址、`status`、`logs`；首次构建可能较慢 | 不因超时删除数据或反复重建，不把 `info` 当健康检查 |
| 本地健康正常，但 `chat` 失败 | 核对模型服务、模型 ID、权限/余额与日志；请求可能收费 | 不把健康检查通过当作模型 Key 已验证 |
| 修改模型参数后没变化 | 重复 `init` / `up` 不覆盖原配置；已有 AstrBot 面板配置独立存在 | 不假设改 `settings.json` 或重传初始化参数必定更新当前模型 |
| 提示非空目录或容器不归本安装 | 找回原实例正确参数；新实例选择新的专用目录 / 运行名，勿放宽私有 ACL | 不删标记文件、不覆盖旧实例、不停止别人的容器 |
| 端口已被占用 | 首次隐式默认冲突可自动选端口并保存；已有原生实例见 [Windows 说明](WINDOWS_NATIVE_FULL.md)，Linux 见端口指南 | 不停止现有实例；不改安装器管理的 `compose.env` |
| 安装好了但 QQ 没反应 | 默认未接 QQ，按下一节单独配置 | 不把“启动成功”理解成已登录、已入群或已送达 |

启动健康等待默认 300 秒；慢机器可用 Linux `up --wait-timeout 600` 或 Windows `up -WaitTimeout 600`（1–3600 秒）。该值不限制原生依赖下载 / Linux 镜像构建总时长，不验证模型或 QQ；原生超时不自动杀实例。

## 可选：外接 QQ

1. 自行获取并安装 NapCat / QQ 或适用的 OneBot 服务，接受其条款。本项目不打包、自动下载或登录它们。
2. 在 AstrBot 面板添加外部 OneBot v11 反向 WebSocket 适配器，明确配置地址、端口和访问令牌。初始候选端口 6199 不代表适配器已启用；原生监听和 Linux 映射均应保持 loopback。其它容器的 `127.0.0.1` 不是宿主地址，连接方式需自行安排；也可按所选外部原生客户端说明手动配置。
3. 需要插件原生 HTTP QQ 操作时，在私有 `settings.json` 中配置连接信息（以下为 JSON 字段示例）：

```json
"onebot": {
  "enabled": true,
  "http_base": "http://你的外部-OneBot-地址:端口",
  "token": "你自行配置的访问令牌"
}
```

连接信息不是 QQ 会话或登录迁移。不要公开无鉴权接口、复用他人登录会话，或未经安排同时用同一 QQ 会话登录多个实例。独立原生 QQ 后端尚未完成，不是现有 OneBot 接入的可替换方案。

## 数据、更新与验证边界

- `settings.json` 包含秘密；Windows 原生限当前用户 SID ACL，Linux 文件 0600 / 目录 0700。原生宿主状态位于 `runtime/astrbot/data`、插件状态在 `higan`；Linux 的 `astrbot` / `higan` 分别保存宿主 / 插件状态。更新前备份整个私有目录并保护备份。
- 重复安装保留密钥、人格、记忆与配置；陌生非空目录或不归本安装的同名容器会被拒绝。没有自动迁移、擦除或接管已有实例的命令；`stop` 也不删除数据。
- 初始模型设置用于生成运行配置。后续已有 AstrBot 面板配置不能被当作仅由初始化文件单向控制；本轮不提供模型配置热更新命令，不承诺手改初始化文件立即生效。插件使用全局 runner 选定的模型；网页聊天的会话级模型下拉覆盖尚未接入插件，不要据下拉选项假定本轮模型已切换。
- 已有不同版本源码 / 插件不会被启动悄悄覆盖；更新需备份和受控流程。完整工具、持久任务与 60/40 聊天区预算保留，但 VM、浏览器、邮件、指针桥接仍需外部服务 / 授权；Linux 依赖不会自动变成 Windows 宿主工具，不能宣称全部外部能力一键可用。
- 原上游许可与声明保留在仓库及镜像中，见 [第三方声明](../THIRD_PARTY_NOTICES.md)。实际构建、模型调用与 QQ 登录验收分别记录；已知构建网络阻塞及未验收事项见 [部署验证记录](QUICK_DEPLOY_VERIFICATION.md)，不能由入口测试替代。
