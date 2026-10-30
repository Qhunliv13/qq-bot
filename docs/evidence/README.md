# 公开证据索引

这里收纳既有 JSON 记录，不改记录内容。按主题选择入口后，再核对每份记录的
版本、时间、授权范围、失败项与未完成项；历史实验不等于当前部署事实。

| 类别 | 阅读说明 | 代表记录 |
|---|---|---|
| 安装与发布 | [部署验证](../QUICK_DEPLOY_VERIFICATION.md)、[本地核心](../LOCAL_FULL_CORE.md) | [预检](quick-deploy-preflight-evidence.json)、[容器](quick-deploy-container-evidence.json)、[本地核心发布](local-full-core-release-20261028.json) |
| 行为修复 | [跨群召回](../CROSS_GROUP_RECALL_FIX.md)、[QQ 延迟](../QQ_LATENCY_FIX_20261028.md)、[回复延迟](../REPLY_LATENCY_FIX_20261028.md) | [召回部署](cross-group-recall-deployment-20261028.json)、[QQ 延迟部署](qq-latency-deployment-20261028.json)、[回复延迟部署](reply-latency-deployment-20261028.json) |
| 静态软件与接口清单 | [传输层研究](../QQ_TRANSPORT_REWRITE.md)、[元数据](../QQ_NATIVE_METADATA.md) | [动作清单](qq-installed-action-inventory.json)、[事件清单](qq-installed-event-inventory.json)、[ABI](qq-native-abi-evidence.json)、[原始软件包](qq-pristine-package-evidence.json) |
| 隔离与原生初始化 | [隔离实验](../QQ_ISOLATED_RUNTIME_LAB.md)、[原生准备](../QQ_NATIVE_READY_CONTROL.md) | [隔离运行](qq-isolated-runtime-evidence.json)、[就绪观察](qq-native-ready-evidence.json)、[准备阶段](qq-prepared-phase-evidence.json) |
| 宿主与总线诊断 | [诊断限制](../QQ_HOST_DIAGNOSTIC_LIMITS.md)、[总线诊断](../QQ_PRIVATE_BUS_DIAGNOSIS.md) | [调度捕获](qq-host-dispatch-capture-evidence.json)、[身份对照](qq-host-identity-evidence.json)、[总线捕获](qq-bus-capture-evidence.json) |
| 网络边界 | [网络出口说明](../QQ_NATIVE_EGRESS_BOUNDARY.md) | [内核 nft 观察](qq-egress-nft-kernel-v3-evidence.json)、[离线 slirp](qq-slirp-offline-evidence.json)、[选择性出口](qq-egress-selective-evidence.json) |

其他同主题快照及对照记录保留原文件名，可直接浏览本目录；这不是完整运行时接口注册表。
[原生 QQ 验收状态](../QQ_NATIVE_ACCEPTANCE_STATUS_20261030.md)仍标明未完成登录、
持续收发与完整功能等价验收，不以证据文件数量替代验收。

维护边界：证据不是秘密配置、账号会话或执行脚本；不向此处复制私有原始日志或凭据。
JSON 内部的历史路径保持原样用于追溯，文档链接和代码读取路径按当前目录维护。
[验收需求清单](../qq-transport-required.json)是审计输入而非实验产物，仍留在文档根目录。
