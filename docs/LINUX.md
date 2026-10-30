# Linux 安装与管理

## 最短安装步骤

准备好 **Bash、宿主 Python 3.10+、已运行且当前用户可访问的 Docker Engine，以及支持 `up --wait` 的 Compose v2**，在完整仓库根目录执行：

```bash
bash ./install.sh
```

按提示填写**模型地址（Base URL）、模型名称（Model ID）、API Key**，Key 隐藏输入。当前安装路径使用 OpenAI 兼容 HTTP 接口，不是任意供应商的原生协议都支持；模型与工具调用能力仍取决于实际服务。可选 QQ 所有者 ID 可以留空，填了也不会自动登录 QQ。

首次构建会下载镜像和依赖、占用时间和磁盘；容器提供 Python 3.12，不需要给宿主执行 `pip install`。安装启动的是完整内嵌 AstrBot 与插件，不是最小替代服务。**不安装 NapCat、不配置 QQ 登录、不接管已有实例**。

默认数据放在仓库外的 **`$HOME/.local/share/qhunliv-bot`**；如果该目录可能已被另一个实例使用，先按后文选择新目录和运行名，不直接运行默认安装。

## 面板与日常管理

默认面板只绑定本机 **http://127.0.0.1:6185**，用户名 **`astrbot`**，密码随机生成。查看当前实例信息或在自己的终端显示面板密码：

```bash
bash ./install.sh info
bash ./install.sh info --show-password
```

`info` 读取私有配置，显示运行名、数据/配置路径、配置中的面板地址与用户名；不需要 Docker、不联网、不启动服务、不调用模型。**配置地址不是健康证明**。默认隐藏密码，显式选项也仅显示面板密码，不显示模型 API Key。这里是私有 `settings.json` 中的初始账号/密码；若在面板改过，以修改后为准，`info` 不重置当前登录信息。不要上传该文件、密码输出或终端截图。

继续使用**同一数据目录和运行名**；新安装选定的端口随初始化保存，后续默认沿用：

| 命令 | 用途与边界 |
|---|---|
| `bash ./install.sh doctor` | 检查已初始化配置、Docker/Compose；不构建、不启动 |
| `bash ./install.sh status` | 查看容器状态，不验证模型凭据或 QQ 登录 |
| `bash ./install.sh health` | 检查本地入口，不调用模型或发送 QQ 消息 |
| `bash ./install.sh logs` | 跟踪最后 100 行；Ctrl+C 只退出日志查看，不停 bot |
| `bash ./install.sh chat --prompt '你好'` | 主动调用同一 bot 的模型，可能收费并执行获授权工具 |
| `bash ./install.sh stop` | 只停止归属校验通过的所选实例，不擦除数据 |
| `bash ./install.sh up` | 用原状态启动，仍可能构建镜像，不是升级或重置 |

`chat` 也接受标准输入，不另建模型循环：

```bash
printf '%s\n' '你好' | bash ./install.sh chat
```

不要把密钥写进提示词、命令行或日志。请求成功只证明这一条模型请求，不代表 QQ 已连接或消息已送达；日志按私有资料处理，分享前脱敏。

## 缺少环境时

启动脚本**不自动安装 Docker、不调用 sudo、不改防火墙、不重启宿主服务**。请按自己的发行版查看 [Docker Engine 安装](https://docs.docker.com/engine/install/)、[Compose 插件安装](https://docs.docker.com/compose/install/linux/)；Ubuntu 可参考 [官方 Ubuntu 指南](https://docs.docker.com/engine/install/ubuntu/)。阅读并自行执行适用的包管理步骤，不把远程下载脚本直接管进 shell。

Docker 访问有高权限含义；添加用户组前先读 [Docker Linux post-install](https://docs.docker.com/engine/install/linux-postinstall/)，或采用官方 rootless 配置。访问失败时自行按官方要求修复，安装器不自动提权或修复 daemon。

以下命令只读，不启动 bot：

```bash
bash --version
python3 --version
docker --version
docker compose version
docker info --format '{{.ServerVersion}}'
```

还需要完整仓库（包括内嵌 AstrBot 源码）、镜像构建所需磁盘/内存、依赖源及模型 API 网络。宿主 Python 用标准库运行安装器，与容器内上游 Python >=3.12 要求不同；[`Dockerfile`](../deploy/Dockerfile) 在容器内提供后者。

## 指定隔离目录与运行名

新实例用**仓库之外的新专用目录**，不要选择已有 bot 数据目录。初始化会拒绝陌生非空目录，不自动导入或覆盖。以下示例需在同一 shell 中执行；新终端要重新设置变量：

```bash
BOT_STATE="$HOME/.local/share/qhunliv-bot-local"
BOT_NAME="qhunliv-bot-local"
bash ./install.sh init --data-dir "$BOT_STATE" --runtime-name "$BOT_NAME"
bash ./install.sh doctor --data-dir "$BOT_STATE" --runtime-name "$BOT_NAME"
bash ./install.sh up --data-dir "$BOT_STATE" --runtime-name "$BOT_NAME"
bash ./install.sh info --data-dir "$BOT_STATE" --runtime-name "$BOT_NAME"
```

- `init` 不需要 Docker，首次写私有配置，Key 隐藏输入；反复执行验证原配置，不覆盖秘密。
- `doctor` 读取已初始化配置，检查环境，不构建、不启动。
- `up` 必要时先初始化，然后预检、构建、启动并等待本地健康。默认等待 **300 秒**，整数范围 1–3600 秒；不限制整个镜像下载/构建总时长。

慢机器可延长健康等待：

```bash
bash ./install.sh up --data-dir "$BOT_STATE" --runtime-name "$BOT_NAME" --wait-timeout 600
```

后续每条管理命令都沿用同样的目录/运行名；例如：

```bash
bash ./install.sh status --data-dir "$BOT_STATE" --runtime-name "$BOT_NAME"
bash ./install.sh health --data-dir "$BOT_STATE" --runtime-name "$BOT_NAME"
bash ./install.sh logs --data-dir "$BOT_STATE" --runtime-name "$BOT_NAME"
bash ./install.sh chat --data-dir "$BOT_STATE" --runtime-name "$BOT_NAME" --prompt '你好'
bash ./install.sh stop --data-dir "$BOT_STATE" --runtime-name "$BOT_NAME"
```

数据包含 `settings.json`、`astrbot` 与 `higan` 状态；备份整个私有目录再做更新。重复 `init` / `up` 不是重置、迁移或现有配置覆盖命令；不能靠删标记文件来绕过所有权保护。初始模型字段是配置生成输入，已有 AstrBot 面板另有运行配置；不保证手改初始化文件或重传参数会更新当前模型，本轮没有模型热更新命令。

## 多实例与端口

不同数据目录/运行名**不会自动避免端口冲突**。[Compose 配置](../deploy/compose.yaml) 的默认 `BOT_WEB_PORT=6185`、`BOT_ONEBOT_PORT=6199` 均绑定 `127.0.0.1`；两端口须不同、在 1–65535 且未被占用。

新安装先核对 16185、16199 未被占用，再在**首次初始化的 Linux 环境**中设置；目录须为该新实例专用：

```bash
BOT_STATE="$HOME/.local/share/qhunliv-bot-second"
BOT_NAME="qhunliv-bot-second"
export BOT_WEB_PORT=16185
export BOT_ONEBOT_PORT=16199
bash ./install.sh init --data-dir "$BOT_STATE" --runtime-name "$BOT_NAME"
unset BOT_WEB_PORT BOT_ONEBOT_PORT
bash ./install.sh doctor --data-dir "$BOT_STATE" --runtime-name "$BOT_NAME"
bash ./install.sh up --data-dir "$BOT_STATE" --runtime-name "$BOT_NAME"
bash ./install.sh info --data-dir "$BOT_STATE" --runtime-name "$BOT_NAME"
```

首次初始化把端口保存至私有设置的 `deployment.web_port` / `deployment.onebot_port`，后续命令默认沿用，不必重复导出。旧安装没有该字段时仍使用原默认值，不自动改写配置。若显式设置上述环境变量，它们只对当前进程临时覆盖已保存值；`info` 只显示所选配置，实际端口变更还需通过 `up` 应用，不能据显示地址判断已监听。

此示例本机面板是 **http://127.0.0.1:16185**，不是默认地址。重复 `init` 不会替已有安装改端口。不要为了换端口编辑安装器管理的 `compose.env`，完整性检查会拒绝。暴露 OneBot 端口不等于已配置适配器或登录 QQ。

远程服务器的 `127.0.0.1` 是服务器自己；在个人电脑访问面板请用 SSH 隧道或经审查的访问方案，不要为照抄教程直接开放面板端口/防火墙或关闭认证。

## 故障排查

| 现象 | 下一步 |
|---|---|
| 找不到配置、提示未初始化 | 核对原用户/目录/运行名；真正的新实例先 `init` 或 `up` |
| Docker / Compose 报错 | 确认 daemon、当前账号权限及 Compose v2；`info` 无需 Docker 仍可查看配置 |
| 启动超时 | 同一实例查 `status` / `logs`，不要删数据或用拆除容器来“修复”健康等待 |
| 面板打不开 | 用 `info` 核对配置端口，再查 `status` / `logs`；远程主机用隧道 |
| `health` 正常但 `chat` 失败 | 检查自己的模型 URL/ID/权限/余额；健康检查没有调用模型 |
| 非空目录、运行名/归属不符 | 找回正确参数，或新安装用新目录和名字；不要覆盖现有实例 |
| 设置更改没有生效 | `init` / `up` 保留已有配置；不要假设初始化文件就是面板当前模型唯一来源 |
| QQ 未登录、消息未发送 | 安装默认不接 QQ，按 [快速开始](QUICKSTART.md#可选外接-qq)单独设置 |

构建、健康、真实模型、QQ 登录各自有独立验收边界；已知网络阻塞等记录见 [部署验证记录](QUICK_DEPLOY_VERIFICATION.md)。不要自动换不可信镜像源或把某条离线测试通过当作实际部署成功。

## 高级参数与入口约定

根 [`install.sh`](../install.sh) 只是共享 [`bootstrap`](../tools/bootstrap.py) 的薄 Bash 入口：无参数默认 `up`，有参数原样传递，保留调用者工作目录、stdin 与 Python 退出码；包含空格的路径须加引号。使用 Bash 而非 `sh`，显式 `bash` 不依赖 Windows checkout 的 executable bit；脚本保持 UTF-8 与 LF。

从其它目录可用绝对路径，如 `bash "/home/me/QQ Bot/install.sh" status --data-dir "$BOT_STATE" --runtime-name "$BOT_NAME"`。相对 `--persona-file ./persona.txt` 按**调用者当前目录**解析，不是仓库目录。带选项时须显式写命令：用 `bash ./install.sh up --data-dir ...`，而不是 `bash ./install.sh --data-dir ...`。

非交互初始化可用 `--api-key-env VARIABLE` 从同一进程环境读取 Key；传的是**变量名而非秘密值**。不在共享启动文件或跟踪文件存 Key；手动安装优先用隐藏输入。详细参数可查看 `python3 tools/bootstrap.py --help`。

## 开发者：离线入口测试

共享 [`unittest fixture`](../tests/test_linux_launcher.py) 用真实 Bash、Linux 私有 `/tmp` 目录下的模拟 `python3` 验证带空格路径、无关 cwd、准确参数（包括 shell 字符和 Unicode）、stdin 与非零退出码；不调用 Docker、不启动 bot、不安装包、不联网。

Linux 无需 pytest 即可运行：

```bash
python3 tools/test_linux_launcher.py
```

Windows 普通测试会验证源码约定，跳过真实 Bash 项；在已有 WSL 内可显式运行同一标准库入口，把示例发行版和路径换成自己的：

```powershell
python -m pytest tests/test_linux_launcher.py -q
wsl -d Ubuntu -- python3 /mnt/c/path/to/checkout/tools/test_linux_launcher.py
```

[`独立 runner`](../tools/test_linux_launcher.py) 加载同一个 fixture，没有第二套 Linux 实现；可执行 fixture 放在 Linux 文件系统而非 Windows 挂载盘。此类离线测试不代表实际模型请求、QQ 登录或完整镜像部署验收。
