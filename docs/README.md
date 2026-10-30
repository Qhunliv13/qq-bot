# 文档导航

第一次使用从 [快速开始](QUICKSTART.md)进入：Windows 双击安装、Linux 一条命令，填写自己的模型地址、模型名与 API Key，再从本地面板或命令行使用。默认不需要 QQ，也不使用作者的账号或服务。

## 安装、使用与管理

| 你要做什么 | 阅读 |
|---|---|
| 完全不懂代码，按图形界面一步一步创建 Bot | [零基础可视化部署](VISUAL_SETUP.md) |
| 安装、找面板密码、排查常见错误 | [快速开始](QUICKSTART.md) |
| 不用 WSL / Docker 的 Windows 原生网页和命令行 | [原生极轻量版](WINDOWS_LITE.md)，双击 `lite.cmd` |
| 完整版 Windows 双击向导或 PowerShell 管理 | [Windows](WINDOWS.md) |
| Linux 安装、私有目录、多实例端口 | [Linux](LINUX.md) |
| 了解完整能力、具体限制与源码目录 | [功能细节](FEATURES.md)（原根 README 技术说明保留） |
| 理解外部配置、账号授权和数据边界 | [配置与仓库边界](CONFIGURATION.md) |
| 核对哪些部署环节已测、哪些尚未验收 | [部署验证记录](QUICK_DEPLOY_VERIFICATION.md) |

`info` 查看配置地址、账号和数据路径，默认隐藏密码；`health` 只查本地入口；`chat` 才主动请求模型，可能收费；`stop` 停止所选且归属验证通过的实例、不删除数据。命令参数与显式显示面板密码方式见平台指南。配置中的地址、容器健康、真实模型请求、QQ 登录与送达不能互相替代。

新安装不接管已有实例。重复初始化不是配置覆盖或升级；初始模型设置与已有 AstrBot 面板运行配置不同，不承诺手改初始化文件即可热更新模型。密钥、密码、聊天档案与私有状态不放进 Git。

## 可选入口与高级用法

- [本地完整核心](LOCAL_FULL_CORE.md)：同一实例的本地入口、权限与结果语义。
- [Lite 文字入口](LITE.md)：明确不含 QQ、面板、工具、持久记忆和后台任务，不替代完整安装指南。
- [工具索引](../tools/README.md)：离线检查、发布工具与有副作用探针的区分；先读边界再运行。
- [第三方声明](../THIRD_PARTY_NOTICES.md)：内嵌 AstrBot 的原始归属与许可证；本项目协议见 [LICENSE](../LICENSE)。

下面是架构、研究和历史证据，不是首次安装所需步骤，也不是生产迁移授权。独立原生 QQ 后端尚未完成登录与功能等价验收。

## 架构与行为

- [核心解耦设计](BOT_CORE_DECOUPLING.md) / [核心测试宿主](BOT_HARNESS.md)：平台边界与离线验证。
- [工具配置](TOOL_PROFILES.md) / [推理输出边界](REASONING_OUTPUT_BOUNDARY.md)：工具可用性与输出约束。
- [事实与图片](FACT_IMAGES.md) / [事实记忆结构](FACT_MEMORY_STRUCTURE_20261028.md)：持久化与来源保留。
- [主动引用](PROACTIVE_QUOTES.md) / [聊天路由和历史](CHAT_ROUTING_AND_HISTORY_FIX.md) / [跨群召回](CROSS_GROUP_RECALL_FIX.md)：原生引用、上下文与权限边界。
- [缓存前缀](CACHE_PREFIX_OPTIMIZATION.md) / [QQ 延迟](QQ_LATENCY_FIX_20261028.md) / [回复延迟](REPLY_LATENCY_FIX_20261028.md)：修复说明与验证范围。
- [指针运行时](VM_POINTER_RUNTIME.md) / [点击检查点](VM_CLICK_CHECKPOINT_20261028.md)：预览、确认与尚未完成的真实桌面验收。

## 原生 QQ：尚未完成

先读 [验收状态与未完成项](QQ_NATIVE_ACCEPTANCE_STATUS_20261030.md)。静态接口清单、
单元测试或隔离实验成功，都不等于已完成真实扫码登录、持续收发或完整功能保留；
不据此替换 NapCat、复用生产会话或切换生产。

- [传输层重写与契约](QQ_TRANSPORT_REWRITE.md) / [验收需求清单](qq-transport-required.json)。
- [后端组合](QQ_NATIVE_BACKEND.md) / [原生宿主](QQ_NATIVE_HOST.md) / [元数据来源](QQ_NATIVE_METADATA.md)。
- [登录](QQ_NATIVE_LOGIN.md) / [会话](QQ_NATIVE_SESSION.md) / [消息](QQ_NATIVE_MESSAGES.md)。
- [网络出口边界](QQ_NATIVE_EGRESS_BOUNDARY.md) / [隔离实验记录](QQ_ISOLATED_RUNTIME_LAB.md)。
- [原生检查器](QQ_NATIVE_INSPECTOR.md) / [准备阶段](QQ_PREPARED_PHASE.md) / [连接观察](QQ_PREPARED_CONNECT.md)。

## 研究与证据

- [证据索引](evidence/README.md)：部署快照、静态清单、隔离与网络实验的原始 JSON。
- [宿主诊断边界](QQ_HOST_DIAGNOSTIC_LIMITS.md) / [私有总线诊断](QQ_PRIVATE_BUS_DIAGNOSIS.md)：观察能证明和不能证明的事项。
- [宿主调度](QQ_HOST_DISPATCH.md) / [捕获](QQ_HOST_CAPTURE.md) / [窗口角色](QQ_HOST_DISPATCH_ROLES.md)：有界诊断及其前提。
- [静态缓存归因](QQ_CACHE_STATIC_ATTRIBUTION.md) / [调用点研究](QQ_CACHE_CALLSITE.md)：静态线索，不是运行时因果证明。

`evidence/` 保存已存在的公开记录，内容按原字节保留；记录中的历史路径、时间、
版本和失败结论不是当前运行配置。其余专项控制文档继续留在本目录，源码留在
`plugin/`、`bot_core/`、`qq_transport/`、`tools/`，第三方源码留在 `vendor/`。
本导航不新增执行入口、不迁移运行数据，也不扩大任何实验或部署授权。
