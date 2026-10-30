# 工具索引

这些脚本依赖各自指定的环境、源码位置和权限。文件名不证明只读、幂等或适用于任意部署；执行前阅读源码和 `--help`。历史一次性补丁位于 [归档](../trash/20261027-repository-cleanup/tools/ops/)，不作为当前操作入口。

## 完整版可视化部署

- 根目录 [install.cmd](../install.cmd) 现以 `-STA` 默认进入 [windows-native.ps1](windows-native.ps1) 的 `wizard`，**Windows 原生完整版**，无需 WSL / Docker / Node；同一固定 AstrBot 4.28.1 与插件，不是 Lite。详细步骤见 [原生完整部署](../docs/WINDOWS_NATIVE_FULL.md)，Python 3.12 全新私有目录默认完整安装已通过，未预置缓存或修改下载；实际完整宿主、面板登录、本地合成模型、排空 / 重启及 PowerShell 5.1 启停也通过。全新电脑双击图形全流程、缺 Python 时安装、真实模型 / QQ 仍未验收。
- [native_runtime.py](native_runtime.py) 复用共享设置 / 浏览器向导和完整上游宿主；命令为 `wizard/init/up/doctor/info/status/stop/logs/health/chat`。`logs` 只显示私有路径；`up` 不自动升级；`stop` 经认证端点请求排空，accepted 不证明退出。默认状态在 `%LOCALAPPDATA%\QhunlivBot\Full`，新目录当前用户 SID 私有 ACL、无提权。
- [native_assets.py](native_assets.py) 将 vendored 核心复制到私有 `runtime/astrbot`，按同版 `pyproject.toml` 完整依赖安装到 `runtime/.venv`；官方 PyPI 同版本预构建 Dashboard 同时核对固定 SHA256 / HTTPS 元数据 / 下载字节，不需 Node，不用 wheel 替换核心，不是作者签名。没有全局 pip 安装；缺 Python 的当前用户 winget 安装必须先明确同意，测试不得执行宿主安装。
- [native_setup_process.py](native_setup_process.py) 仅监督 Windows 10+ 的有限安装步骤：原子 Windows Job 归属和仅 NUL 句柄继承，超时收回专属后代树，清理未确认则保留重试保护。`BOT_NATIVE_SETUP_TIMEOUT` 默认每步 1800 秒，可设 1–86400；不限制已运行 Bot，不自动重复安装，也不修复网络下载速度。
- 原 [windows-setup.ps1](windows-setup.ps1) / [Windows 图形提示层](windows-setup-ui.ps1)、[windows.ps1](windows.ps1) 与根 `install.ps1` 保留为**显式 WSL / Docker 兼容**入口，不删除，不管理新的原生状态；见 [WSL 指南](../docs/WINDOWS_WSL.md)。
- [bootstrap.py](bootstrap.py) 的 `wizard` 命令通过 [setup_wizard.py](setup_wizard.py) 与 [本地固定页面](setup_wizard.html) 提供五步部署 UI；Linux 无参数入口也进入向导。只绑定 loopback，授权片段仅留在标签页内存，修改请求要求精确 Host / Origin / Bearer，状态不导出密钥。
- 初始化、启停和试聊复用同一完整部署能力；试聊需明确确认并可能计费或调用工具，不是只读检查。原生本地端点为 loopback + operator token + 当前用户 SID ACL，不是 Linux Unix socket，也不隔离同用户恶意进程。重复打开不覆盖配置；已实测启动父进程退出后 detached 宿主续跑及本地在途排空 / 实际退出；未验收整个终端关闭、用户注销或真实 QQ / 外部工具排空。QQ / NapCat 不打包、不自动登录；VM、浏览器、邮件、指针桥接等外部 / Linux 依赖不自动成为宿主工具。见 [零基础指南](../docs/VISUAL_SETUP.md)。

## Windows 原生轻量入口

- 根目录 [`lite.cmd`](../lite.cmd) / [`lite.ps1`](../lite.ps1) → [`windows-lite.ps1`](windows-lite.ps1) → [`lite_native.py`](lite_native.py)：原生 Python 3.10+ 标准库，无 WSL / Docker / pip；网页与命令行文字对话。首次写仓库外独立设置，Windows Key 用当前用户 DPAPI 加密；实际聊天才请求模型。不接 QQ、不执行工具、不保留磁盘聊天记录。
- [`build_lite.py`](build_lite.py)：按固定源码允许清单生成新 ZIP，目标必须不存在且在仓库外；不下载、不打包设置或 Python 解释器。见[轻量使用说明](../docs/WINDOWS_LITE.md)。

## 离线检查

在仓库根目录执行：

```bash
python -m pytest tests -q
python -m compileall -q plugin
python tools/validate_release.py
```

- [validate_release.py](validate_release.py)：检查插件和工具文件的语法及凭据模式。
- [tool_contract_audit.py](tool_contract_audit.py)：工具契约与真实分发代码的离线审计；外部接口模拟结果不是线上操作成功证明。
- [qq_transport_parity_audit.py](qq_transport_parity_audit.py)：只比对候选声明与现有 QQ 契约；加 `--installed-inventory docs/evidence/qq-installed-action-inventory.json` 检查 180 个基础动作及 360 个别名。完整声明不代表运行验证，不授权切换生产。
- [qq_native_preflight.py](qq_native_preflight.py)：仅对显式 `--qq-root` 软件目录做有界静态检查，不执行 QQ／原生库、不读取账号目录。架构识别不代表 ABI 兼容；始终返回未就绪（退出码 1）。

- [qq_asar_inventory.py](qq_asar_inventory.py)：对显式 `--archive` 路径仅读取有界 ASAR header，校验条目元数据与偏移；不读取条目负载、不解包、不执行代码，不能据此恢复 QQ 原始入口或证明接口可用。

- [qq_isolated_runtime_probe.py](qq_isolated_runtime_probe.py)：默认只校验路径；仅显式 `--execute` 在 Linux 私有网络／挂载／PID 命名空间与空 chroot 内，以降权子进程读取运行时版本。无账号、无应用入口或 native 导入；隔离不成立则拒绝执行，不授权登录或生产切换。使用前阅读 [隔离实验边界](../docs/QQ_ISOLATED_RUNTIME_LAB.md)。

## 发布与配置

原私有部署的 `release_apply.py`、`release_access.py`、`sid_native_disable.py` 和 `runtime_routing_config.py` 作为完整依赖组保存在 [2026-10-30 历史归档](../trash/20261030-personal-operations/README.md)。它们包含原部署的路径、端口、服务名或版本绑定，不是当前安装、发布或配置入口；归档并不授权执行。对应离线回归测试仍保留。

| 工具 | 用途与边界 |
|---|---|
| [set_group_policy.py](set_group_policy.py) | 管理群策略；会写配置 |
| [command_toggle.py](command_toggle.py) | 管理命令状态；会改变运行配置 |
| [task_deadline_review.py](task_deadline_review.py) | 审核绝对执行时间变更；默认预览，应用前校验任务快照 |
| [task_cadence_review.py](task_cadence_review.py) | 审核任务周期变更；默认预览，应用前校验任务快照 |

## 运行探针

以下工具可能联网、调用真实模型、读取实际运行信息或创建隔离验证状态。不要把所有探针统一当作无副作用脚本；它们不是自动接受 QQ 申请或发送测试消息的授权。

| 类别 | 入口 |
|---|---|
| 模型与工具回路 | [production_probe.py](production_probe.py)、[batch_model_probe.py](batch_model_probe.py)、[model_simulation_audit.py](model_simulation_audit.py) |
| 回复与群策略 | [interim_reply_probe.py](interim_reply_probe.py)、[reply_finish_probe.py](reply_finish_probe.py)、[structured_silence_probe.py](structured_silence_probe.py)、[mention_policy_probe.py](mention_policy_probe.py) |
| 发布与草稿 | [explicit_publication_probe.py](explicit_publication_probe.py)：原生工具回路与自主适配器的显式发布边界；模型和平台均替换为本地记录器，不向 QQ 发消息 |
| 转发与复制粘贴 | [message_transfer_probe.py](message_transfer_probe.py)：真实分发、剪贴板与防重流程，平台接口替换为本地记录器，不执行真实转发 |
| 事实与队列 | [fact_queue_probe.py](fact_queue_probe.py)：临时事实库、只读 worker 目录、任务认领边界和锁等待合批；不改真实记忆，不验证真实入口耗时 |
| 指令来源与条件 | [instruction_provenance_probe.py](instruction_provenance_probe.py)：真实模型、小型合成上下文验证历史条件与当前触发分离；工具副作用禁用，不代替真实大上下文行为验证 |
| 上下文与附件 | [attachment_read_probe.py](attachment_read_probe.py)、[forward_read_probe.py](forward_read_probe.py)、[nontext_read_probe.py](nontext_read_probe.py)、[file_source_probe.py](file_source_probe.py) |
| 能力与主动观察 | [capability_probe.py](capability_probe.py)、[dsh_capability_probe.py](dsh_capability_probe.py)、[autonomy_read_probe.py](autonomy_read_probe.py) |
| 缓存与历史 | [shared_history_cache_probe.py](shared_history_cache_probe.py) 默认离线对照精确滚窗和 30 条块对齐：保留最新 240 条及完整来源，比较实际序列化前缀；显式 `--live` 才发四次隔离模型请求，不执行工具或发送 QQ。[prompt_structure_cache_probe.py](prompt_structure_cache_probe.py) 默认离线比较同内容布局，显式 `--live` 才发四次隔离模型请求。真实命中须读提供方 token，合成场景不代表生产整体收益。[cache_layout_probe.py](cache_layout_probe.py) 必须显式传入 `--data-root`，它读取该目录的真实历史／观察并向已配置模型发送四次诊断请求（无 QQ 发送或工具执行）。[cache_prefix_probe.py](cache_prefix_probe.py)、[history_cache_probe.py](history_cache_probe.py) |
| 图片发现 | [image_search_probe.py](image_search_probe.py) |

密钥和运行数据库不随源码分发。输出记录应保存在受保护的独立目录，不提交聊天内容、原生请求 flag、二维码或令牌。
