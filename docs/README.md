# 文档导航

按使用目的阅读，不把研究记录当作部署步骤。新安装不会接管已有生产实例；
独立原生 QQ 后端仍未完成登录与功能等价验收。

## 用户部署

| 入口 | 用途 |
|---|---|
| [快速开始](QUICKSTART.md) | 新安装、外置配置、本地调用与可选外接 QQ |
| [配置与仓库边界](CONFIGURATION.md) | 无个人默认身份、数据目录与服务地址配置、旧运维归档 |
| [Windows](WINDOWS.md) / [Linux](LINUX.md) | 各平台安装与运行要求 |
| [部署验证记录](QUICK_DEPLOY_VERIFICATION.md) | 已验证项目、实际失败与未验收边界 |
| [本地完整核心](LOCAL_FULL_CORE.md) | 现有完整 bot 的本地入口、权限与结果语义 |
| [轻量入口](LITE.md) | 另一种运行入口的适用范围，不替代完整安装指南 |
| [工具索引](../tools/README.md) | 离线检查、发布工具与有副作用探针的区分 |

## 架构与行为

- [核心解耦设计](BOT_CORE_DECOUPLING.md) / [核心测试宿主](BOT_HARNESS.md)：平台边界与离线验证。
- [工具配置](TOOL_PROFILES.md) / [推理输出边界](REASONING_OUTPUT_BOUNDARY.md)：工具可用性与输出约束。
- [事实与图片](FACT_IMAGES.md) / [事实记忆结构](FACT_MEMORY_STRUCTURE_20261028.md)：持久化与来源保留。
- [聊天路由和历史](CHAT_ROUTING_AND_HISTORY_FIX.md) / [跨群召回](CROSS_GROUP_RECALL_FIX.md)：上下文与权限边界。
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
