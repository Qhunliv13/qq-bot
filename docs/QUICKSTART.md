# 快速开始

在自己的电脑或 Linux 服务器上运行彼岸酱（Qhunliv-bot）：安装、填写自己的模型设置，再从本地面板或命令行使用。**不需要先连接 QQ**；QQ 是可选接入，不随安装自动登录。

## 1. 启动安装

先下载并**完整解压**仓库 ZIP，或克隆完整仓库；不要只下载单个启动脚本。

| 平台 | 在仓库根目录操作 | 需要知道 |
|---|---|---|
| Windows | 双击 [`install.cmd`](../install.cmd) | 向导检查 WSL2、Linux Python 与 Docker；缺依赖时先征求同意，不强制重启电脑 |
| Linux | 运行 `bash ./install.sh` | 预先准备 Python 3.10+、可访问的 Docker Engine 与支持 `up --wait` 的 Compose v2；脚本不自动安装系统依赖 |

首次构建需要下载基础镜像及依赖，并占用磁盘、内存和时间；不是离线安装。AstrBot v4.28.1 的前后端源码已内嵌，容器提供 Python 3.12；上游 Python 依赖没有完整锁定，因此不承诺逐字节可复现。Windows 不需要另装 Windows Python。

Windows 提示安装 WSL、重启或初始化 Ubuntu 时，按系统提示完成，再次双击继续；已有 Docker Desktop 请开启所选发行版的 **Resources → WSL Integration**。详见 [Windows](WINDOWS.md) / [Linux](LINUX.md)。

## 2. 填写三项模型设置

| 提示项 | 填什么 | 注意 |
|---|---|---|
| 模型地址（Base URL） | 你的模型服务给出的 HTTP(S) API 地址 | 按服务说明决定是否带 `/v1`；不要把完整聊天请求路径当作 Base URL |
| 模型名称（Model ID） | 该服务实际提供的模型 ID | 不是随意填写的显示名 |
| API Key | 你自己的有效模型凭据 | 安装时隐藏输入；不要写进命令行、聊天、Git 或截图 |

当前安装路径使用 **OpenAI 兼容的 HTTP 接口**，不是任意供应商原生协议的通用适配器；具体模型与工具调用能力仍需验证。可选 QQ 所有者 ID 可以留空，不填写不会授权任何 QQ 账号，也不会启用 QQ 登录。初始人格默认为空文本，需要时可在初始化时用 `init --persona-file` 提供原文。

安装会创建仓库之外的私有数据目录，默认在 Linux 用户的 `~/.local/share/qhunliv-bot`；Windows 数据也在所选 WSL 发行版中。**记住所选发行版、数据目录与运行名**，后续管理同一实例时保持一致。首次初始化所选端口会保存在私有配置中；旧安装未保存时仍用原默认值，见 [端口说明](LINUX.md#多实例与端口)。

## 3. 打开面板

启动成功后打开默认本机地址 **http://127.0.0.1:6185**；Windows 双击向导会尝试自动打开。面板用户名为 **`astrbot`**，密码随机生成。

| 查什么 | Linux | Windows PowerShell（仓库根目录） |
|---|---|---|
| 地址、用户名、数据目录与配置路径 | `bash ./install.sh info` | `.\install.ps1 info` |
| **仅在自己的终端**显示面板密码 | `bash ./install.sh info --show-password` | `.\install.ps1 info -ShowPassword` |

`info` 不需要 Docker，不启动服务、不访问网络，也不调用模型；默认隐藏密码，显式显示的也只有面板密码，**不显示模型 API Key**。它显示的是配置中的地址，**不是运行就绪证明**。这里显示的是私有 `settings.json` 中的初始用户名/密码；如果你已在面板中改过登录信息，初始值可能不再有效，`info` 不重置密码。不要分享整个文件、密码输出或终端截图。

若 PowerShell 执行策略阻止直接运行脚本，可用 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 info`；其它命令同理。此 Bypass 只作用于本次进程，不修改系统执行策略。

## 4. 日常使用与管理

以下默认管理初次安装的同一实例。若 Windows 向导打印了带 `-Distro`、`-User` 或 `-DataDir` 的管理命令，请沿用实际参数，不照抄另一套默认值。

| 目的 | Linux | Windows PowerShell | 副作用与判断 |
|---|---|---|---|
| 环境预检 | `bash ./install.sh doctor` | `.\install.ps1 doctor` | 读取配置并检查 Docker，不构建或启动 |
| 查看容器状态 | `bash ./install.sh status` | `.\install.ps1 status` | 不验证模型凭据或 QQ 登录 |
| 本地入口健康 | `bash ./install.sh health` | `.\install.ps1 health` | 不调用模型、不发送 QQ 消息 |
| 跟踪日志 | `bash ./install.sh logs` | `.\install.ps1 logs` | Ctrl+C 退出日志查看，不停止 bot；日志须脱敏 |
| 主动发起一次本地请求 | `bash ./install.sh chat --prompt '你好'` | `'Hello' \| .\install.ps1 chat` | 会调用自己的模型，可能产生费用，并可能按请求执行工具 |
| 停止此实例 | `bash ./install.sh stop` | `.\install.ps1 stop` | 只停止归属校验通过的所选实例，不删除数据 |
| 启动或恢复运行 | `bash ./install.sh up` | `.\install.ps1 up` | 复用原数据，仍可能构建镜像；不是升级或重置命令 |

`chat` 不另建一套模型循环，调用的是同一实例的完整本地入口；也可不带 `--prompt`，从标准输入读取。Linux 本地 socket 使用 root peer credentials；结果进入原历史，不伪造 QQ 送达。模型成功、服务健康、QQ 已登录和 QQ 消息已送达是四种不同结果。

## 遇到问题先看这里

| 现象 | 先做什么 | 不要做什么 |
|---|---|---|
| 不记得地址或面板密码 | 对同一实例运行 `info`；需要密码时显式加显示选项 | 不上传 `settings.json`，不重新初始化来找密码 |
| 提示还未初始化 | 用原安装入口运行 `up`，或先 `init`；核对目录与发行版 | 不把另一个实例的私有目录当作新目录 |
| Docker / Compose 不可用 | 运行 `doctor`；Windows 检查 Desktop 和 WSL Integration | 不换未知镜像源、不关闭 TLS、不擅自替换已有 Docker |
| 启动超时或面板打不开 | 核对 `info` 的配置地址、`status`、`logs`；首次构建可能较慢 | 不因超时删除数据或反复重建，不把 `info` 当健康检查 |
| 本地健康正常，但 `chat` 失败 | 核对模型服务、模型 ID、权限/余额与日志；请求可能收费 | 不把健康检查通过当作模型 Key 已验证 |
| 修改模型参数后没变化 | 重复 `init` / `up` 不覆盖原配置；已有 AstrBot 面板配置独立存在 | 不假设改 `settings.json` 或重传初始化参数必定更新当前模型 |
| 提示非空目录或容器不归本安装 | 选择新的专用目录/运行名，或找回原实例的正确参数 | 不删标记文件、不覆盖旧实例、不停止别人的容器 |
| 端口已被占用 | 按 [Linux 端口说明](LINUX.md#多实例与端口)选择未占用端口 | 不停止现有实例；不改安装器管理的 `compose.env` |
| 安装好了但 QQ 没反应 | 默认未接 QQ，按下一节单独配置 | 不把“启动成功”理解成已登录、已入群或已送达 |

启动健康等待默认 300 秒；慢机器可用 Linux `up --wait-timeout 600` 或 Windows `up -WaitTimeout 600`（1–3600 秒）。该值不限制镜像构建总时长，不验证模型或 QQ。

## 可选：外接 QQ

1. 自行获取并安装 NapCat / QQ 或适用的 OneBot 服务，接受其条款。本项目不打包、自动下载或登录它们。
2. 在 AstrBot 面板添加外部 OneBot v11 反向 WebSocket 适配器，明确配置地址、端口和访问令牌。6199 默认仅映射宿主 loopback；其它容器的 `127.0.0.1` 不是宿主地址，连接方式需自行安排。
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

- `settings.json` 包含秘密，Linux 文件权限为 0600，目录为 0700；`astrbot` 与 `higan` 子目录分别保存宿主和插件状态，更新前备份整个私有目录。
- 重复安装保留密钥、人格、记忆与配置；陌生非空目录或不归本安装的同名容器会被拒绝。没有自动迁移、擦除或接管已有实例的命令；`stop` 也不删除数据。
- 初始模型设置用于生成运行配置。后续已有 AstrBot 面板配置不能被当作仅由初始化文件单向控制；本轮不提供模型配置热更新命令，不承诺手改初始化文件立即生效。
- 已有不同版本插件不会被容器启动悄悄覆盖；更新需备份和受控流程。虚拟机、邮件等工具另需真实服务与授权，目录中有工具不等于已经可用。
- 原上游许可与声明保留在仓库及镜像中，见 [第三方声明](../THIRD_PARTY_NOTICES.md)。实际构建、模型调用与 QQ 登录验收分别记录；已知构建网络阻塞及未验收事项见 [部署验证记录](QUICK_DEPLOY_VERIFICATION.md)，不能由入口测试替代。
