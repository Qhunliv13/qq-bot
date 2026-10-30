# Windows 原生极轻量版

**不需要 WSL、Docker、AstrBot、pip、Node.js 或管理员权限。** 只需本机已经安装的 **Python 3.10+** 和自己的 OpenAI 兼容模型服务；本地启动无需安装任何 Python 第三方依赖。它不是本地大模型，模型请求仍发往你配置的服务，可能产生费用。

## 双击就用

1. 安装 Python 3.10+（已有可用 Python 无需重复安装）。若提示找不到 Python，安装时启用启动器 `py` 或将 Python 加入 PATH，然后重新打开。入口不会自行下载安装软件或修改 PATH。
2. 完整解压轻量包或仓库，双击根目录 **[`lite.cmd`](../lite.cmd)**。
3. 首次填写模型 **Base URL、Model ID、API Key**；填错只重填当前项目。不要把 `/chat/completions` 完整请求路径当 Base URL。远程服务要求 HTTPS，本机服务可以使用 `http://127.0.0.1:端口/v1` 等回环地址。
4. 自动打开本机网页，输入消息即可。默认随机选择一个空闲的 `127.0.0.1` 端口，避免冲突；关闭终端或按 Ctrl+C 停止。退出后再次双击会复用设置。

没有内置模型地址、作者账号、服务器、固定人设或 QQ 号。默认系统提示词为空，按需用 `-PersonaFile` 指定自己的 UTF-8 文本。

## 网页和命令行，两种模式

在解压目录打开 PowerShell（路径包含空格、中文也可）：

```powershell
# 默认打开本机网页
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\lite.ps1
# 原生窗口一问一答，输入 /exit 退出
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\lite.ps1 chat
# 只看配置位置和存储类型；不显示 Key，不访问模型
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\lite.ps1 info
# 显式修改设置；Enter 保留现有值，Key 隐藏输入
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\lite.ps1 configure
# 不自动打开浏览器；手动复制终端打印的完整本机链接
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\lite.ps1 web -NoBrowser
```

执行策略 `Bypass` 仅作用于本次 PowerShell 进程，不修改系统策略。也可直接运行 `python -X utf8 -B tools/lite_native.py chat`，不依赖 PowerShell 脚本。

网页无需外部脚本、字体或 CDN，使用浏览器原生 API。不渲染模型返回的 HTML。界面支持 Enter 发送、Shift+Enter 换行、输入法与中文/Emoji。

**授权链接仅供本机自己使用。** 链接末尾的 `#...` 是本次运行的随机授权片段，不是模型 Key；页面加载后从地址栏移除且不发给 HTTP 服务器。不要分享、截图或转发完整链接。刷新页面后需重新打开终端中的完整链接；重启进程会换新授权。服务拒绝跨站 Origin、错误 Host、缺失令牌的数据请求，只监听回环地址，不提供公网访问功能。

同一服务的所有标签页**共享同一段有限记忆**；“清空全部”影响该服务的所有标签页。默认保留最近 12 个成功问答，使用 `-HistoryTurns 20` 调整（1–1000）。失败请求不写入记忆；聊天记录仅在内存，关闭进程丢弃。需要不同对话时，可分别启动新的进程。固定端口可用 `-Port 16185`，占用时明确报错，不停止别的服务。

## 私有设置和 API Key

默认目录：**`%LOCALAPPDATA%\QhunlivBot\Lite`**，与完整版本和仓库完全分开。`info` 显示当前选定目录；使用自定义 `-DataDir 'D:\我的数据\Lite'` 时，每次命令使用同一参数。不要指向完整部署目录、现有无关目录或仓库内目录。

- Windows 交互输入的 Key 经 **Windows 当前用户 DPAPI** 加密，写入私有设置；不以明文写入，不传命令行、不发给网页。
- 加密依赖当前 Windows 用户/机器环境；把设置复制给另一台电脑或用户，不保证能解密，应在新环境重新配置。不提供密码/Key 明文显示命令。
- 用户自定义系统提示词、模型名和服务地址属于私有设置，但不是加密内容。设置仍应妥善保管；DPAPI 不保护已被同一用户的恶意软件控制的进程。
- 如需自动化，可传 `-ApiKeyEnv YOUR_KEY_VARIABLE -NonInteractive -ModelBase 'https://your-provider.example/v1' -Model '实际模型ID'`。参数是**环境变量名**，实际 Key 要先在自己的环境安全设置；此方式只保存变量名，后续启动需该变量仍可用，不永久复制环境 Key。
- 不安全的隐藏输入终端会停止，不降级成回显。`configure` 保存设置不调用模型；`info` 不验证网络、Key 是否有效或模型权限。

## 轻量版与完整版

| 项目 | 原生极轻量版 | 完整版 |
|---|---|---|
| Windows 运行 | Windows Python 3.10+，标准库 | WSL2 + Docker + 内嵌 AstrBot |
| 界面 | 极小本机网页 + 命令行 | AstrBot 管理面板 |
| 文本多轮对话 | 是，内存历史 | 是 |
| QQ 登录/收发 | **无** | 需额外接入、另行验收 |
| 工具、文件、图片、持久记忆、后台任务 | **无** | 保留完整插件功能 |
| 默认入口 | `lite.cmd` | `install.cmd` |

这是独立、明确选择的轻量模式，**不替换、不迁移、不自动降级完整安装**。请求复用原有 `bot_core.http_host.ChatCompletionHost` 和轻量回复实现，不维护另一套模型传输逻辑。若上游返回工具调用，轻量模式拒绝执行而不是伪造成功。

## 仅打包必需源码

维护者可运行：

```powershell
python -B tools/build_lite.py --output 'C:\一个仓库外目录\qhunliv-windows-lite.zip'
```

按固定允许清单生成小型 ZIP，仅含本机入口、标准库轻量核心、必需共享源码、本文档与许可证；不包含 vendor、Docker、完整插件、个人配置、缓存、凭据或 Python 解释器，不执行任何下载。输出目标必须不存在且位于仓库外，避免覆盖文件。ZIP 解压后仍需 Windows Python 3.10+，**不是免 Python 的 EXE**。

## 本轮验证范围

- Windows 当前用户 DPAPI 实际加解密回环及密文配置保存通过；标准输出和网页不包含测试密钥。
- 真实 Windows PowerShell 5.1 → 根启动器 → 原生 Python → 合成回环 HTTP 模型，验证两轮中文/Emoji 和上下文连续；不是只替换启动器的 mock。
- 从最小 ZIP 独立解压，在其它工作目录运行 Python `-S` 禁用第三方 site-packages，实际启动网页进程，验证授权、两轮回复、历史与清空；无 WSL、Docker 或 vendor 参与。
- 全仓复测：**4779 passed、44 skipped、442 subtests passed**。首次全仓有一项既有 VM 时序测试失败，相关 75 项单测重跑及第二次全仓均通过；未修改 VM 逻辑。
- 测试使用合成回环模型，不代表任意供应商真实 Key、计费、模型兼容性或浏览器像素级渲染已验收。不访问历史账号、生产 QQ、WSL 或 Docker。

## 排错

- **找不到 Python**：确认新打开的窗口能执行 `py -3 --version` 或 `python --version`，版本至少 3.10；不要为此安装 WSL。
- **打开的是完整安装向导**：应双击 `lite.cmd`，不是 `install.cmd`。
- **网页刷新后未授权**：重新打开当前终端打印的完整片段链接；无需重填模型信息。
- **请求失败**：核对自己的 Base URL、模型 ID、Key、余额与服务可用性；错误不回显上游秘密，不等于“模型健康通过”。
- **Key 解密失败**：可能更换了 Windows 用户或机器，用 `configure` 重新输入自己的 Key；不删除已有完整版数据。
- **端口被占用**：默认随机端口；显式固定端口时换一个或自行处理占用，不自动杀进程。
