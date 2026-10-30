# Qhunliv-bot 全系统架构、模块状态与运行逻辑

> 文档日期：2026-10-30，Asia/Shanghai（UTC+08:00）。范围：本仓库完整 Bot、安装与 UI、Lite、Harness、QQ 接入与实验支线，不含无关 Q-API 计费系统。依据：第一方源码逐模块审计，以及本轮上下文、摘要和唤醒记录修复的离线回归；修复基线提交为 `4528c78`。最终发布提交以包含本文的 Git 提交为准，避免把自身提交号写入自身造成循环。
>
> **“有实现”“离线通过”“依赖外部服务”“真实验收未完成”分别标记。没有连接生产、读取私有聊天库、登录 QQ、付费调用模型或部署实例；源码已修不等于你的线上 Bot 已经更新。**

## 1. 阅读结论与系统边界

1. **完整 Bot 的实际宿主仍是内嵌 AstrBot + `HiganPlugin`**。主入口不是 `bot_core`：容器先由 [entrypoint](../deploy/entrypoint.py#L164-L180) 校验/初始化外部数据，再 `exec` 固定上游 `main.py`；AstrBot 加载 [插件入口](../plugin/main.py)。模型、工具、记忆、前台消息、主动观察和持久任务都由该插件装配。
2. **完整安装器、本地同实例入口、独立 Harness、原生 Lite 是不同层级**。安装器不另造 Agent；`local.sock` 接入同一个插件；Harness 只抽出注入式工具循环；Lite 是标准库文字聊天，不含完整工具/QQ/持久记忆/任务。见 [本地入口](../plugin/local_runtime.py#L1-L5)、[Harness](../bot_core/harness.py#L1-L7)、[Lite](../tools/lite_native.py#L1)。
3. **QQ 现有接入依赖外部 OneBot/NapCat**，fresh install 不启用平台、不打包 QQ 客户端或 NapCat、不自动登录。独立 `qq_transport` 有离线机制和 dormant JS 生命周期，但完整 native 后端、独立登录和真实全动作实现未完成；不得据静态 action 清单或模拟测试替换现有接入。[fresh 配置](../deploy/entrypoint.py#L113-L116)、[独立后端状态](../qq_transport/native_backend.js#L221-L232)。
4. **持久状态分两大根**：AstrBot 的 `data/` 保存运行配置、账号面板数据、插件链接和部分媒体；`HIGAN_ROOT` 保存机器人档案、事实、任务、观察、策略和操作日志。二者都可能含私密信息，不在源码树里自动探测或自动迁移。[路径解析](../plugin/runtime_paths.py#L7-L22)、[Compose 挂载](../deploy/compose.yaml#L21-L41)。
5. **工具声明 ≠ 依赖已就绪 ≠ 操作成功 ≠ 对方实际读到**。HTTP 200、服务健康、API 接受、原生消息号、业务回读是不同证据等级。部署健康只验证本地 intake 与面板 HTML；不验证模型 Key、QQ 登录、VM、邮件和送达。[健康检查](../deploy/healthcheck.py#L18-L42)、[QQ 回执](../plugin/qq_delivery.py#L58-L88)。
6. **权限并非全工具统一 owner-only**。真实代码对账号申请、社交写入、skill 保存、主动计划分别有门禁；普通前台群策略、任务管理及 VM/workspace 并非都经过同一管理员检查。本文记录现状，不宣称已经完成统一权限加固。详见第 7 节。

## 2. 总体分层与真正入口

```text
Windows install.cmd ─ windows-setup.ps1 / UI ─ prerequisites ─ windows.ps1 ┐
Windows install.ps1 ──────────────────────────────────── windows.ps1 ───┤
Linux install.sh ──────────────────────────────────────────────────────┤
                                                                      v
                     tools/bootstrap.py（外部 state、doctor、Compose、管理）
                         ├─ setup_wizard.py + setup_wizard.html（loopback UI）
                         └─ deploy/compose.yaml → Dockerfile → entrypoint.py
                                                            |
                                                            v
                vendor/astrbot/main.py（固定 AstrBot 4.28.1 宿主/面板/平台）
                                  |
                 data/plugins/astrbot_plugin_higan → plugin/
                                  |
                          HiganPlugin.initialize
               ┌──────────────────┼──────────────────────┐
               v                  v                      v
          on_message        LocalRuntime            后台循环
      QQ / WebChat等   Linux root Unix / Windows私有ACL+token   autonomy / tasks / 成员观察
               └──────────────────┼──────────────────────┘
                            单实例 execution_lock
                                  |
                 _decide_tools_first → plugin.agent_loop.run
                    模型 transcript ↔ 工具分发 ↔ 观察归档
                                  |
                       输出协议 → 发布选择 → _send
                 ┌────────────────┼───────────────────┐
                 v                v                   v
             AstrBot event     OneBot HTTP        本地 record_output
                                  |
                           外部 QQ/NapCat

独立复用：bot_core.Harness → 同一 plugin.agent_loop（无完整宿主与QQ身份）
原生 Lite：lite.cmd/ps1 → lite_native.py → lite_web.py / lite.py → ChatCompletionHost
实验支线：qq_transport（Python journal/outbox/HTTP + JS native controllers）
          + tools/qq_*（显式隔离实验）；目前未接入上面的完整运行路径
```

### 2.1 根启动器逐项

| 文件 | 实际职责与边界 |
|---|---|
| [install.cmd](../install.cmd#L1-L9) | 双击默认原生完整入口；转交Windows PowerShell的windows-native.ps1，Python缺失须同意用户级安装，无WSL/Docker/Lite隐式依赖；原WSL安装助手显式保留。 |
| [install.ps1](../install.ps1#L1-L15) | 薄转发层，保留原始参数与 pipeline 输入到 `tools/windows.ps1`；异常不回显私密参数。 |
| [install.sh](../install.sh#L1-L21) | Bash 校验、定位自身目录、检查 python3；无参数进 `wizard`，最后 `exec bootstrap.py`。不安装 Linux 系统依赖。 |
| [lite.cmd](../lite.cmd#L1-L10) | 可选原生轻量双击入口，转交 `lite.ps1`；不是完整安装器的隐式降级。 |
| [lite.ps1](../lite.ps1#L1-L14) | 薄转发到 `windows-lite.ps1`，传递 pipeline；不读取模型配置。 |

### 2.2 完整启动生命周期

Windows默认新增[原生完整部署](WINDOWS_NATIVE_FULL.md)：[windows-native.ps1](../tools/windows-native.ps1)→[native_runtime.py](../tools/native_runtime.py)→[native_assets.py](../tools/native_assets.py)。复用原安装网页和deploy.prepare配置语义，独立venv安装vendored pyproject全部依赖、复制固定核心/插件、SHA256固定同版本面板文件，无Node/WSL/Docker宿主依赖；Windows私有SID ACL+token本地通道复用同一插件回路。原容器/WSL与Lite仍保留；真实原生完整实装和QQ/外部桥能力分别验收，见[验证记录](QUICK_DEPLOY_VERIFICATION.md)。

- [bootstrap](../tools/bootstrap.py#L38-L82) 将数据目录限制在仓库外，也不接受源码目录祖先；默认安装路径是当前运行账号下 `.local/share/qhunliv-bot`。实例名可明确指定，否则由数据路径 hash 隔离。
- [initialize_settings](../tools/bootstrap.py#L144-L189) 验证后只初始化 fresh state：创建 `astrbot/`、`higan/`、`config/`，写私有 `settings.json`、`compose.env`，最后才写 `.bootstrap.json`。已有受管安装仅核验，不重置密钥；外来非空目录拒绝接管。
- [Compose](../deploy/compose.yaml#L1-L47) 运行一个 `bot` 服务，面板 host port 默认 6185，可选 OneBot host port 默认 6199，均绑定宿主 loopback。未启用任何 QQ adapter。`restart: unless-stopped`，`stop_grace_period: 120s`；`run/` 放 tmpfs，避免容器重建保留过期 Unix socket。
- [entrypoint.prepare](../deploy/entrypoint.py#L46-L141) 对 bundled plugin 全文件内容算 SHA-256，保存在 `.qhunliv-bootstrap.json`。旧 volume 使用不同插件镜像时拒绝静默升级；非空外来 AstrBot data 拒绝自动迁移。插件是指向镜像只读源码的 symlink，不复制为第二套实现。
- [entrypoint.main](../deploy/entrypoint.py#L144-L180) 从 vendored defaults AST 表达式取得默认值，避免提前 import core 造成 DB/初始账号副作用；检查 bundled dashboard 与上游版本一致，再 `exec` 上游。
- [HiganPlugin](../plugin/main.py) 创建一个执行锁、scope 队列和后台集合，初始化时先排空旧实例，随后启动媒体描述、成员观察、主动机会、任务循环和本地 Unix 入口。旧实例停止接收后，等待已经开始的模型/工具/发送完成，不把热加载等同于强制取消。

## 3. 第一方 plugin 模块完整分组清单

以下每个链接均指向实际源码；职责来自模块接口及调用关系，而非文件名推测。函数名和源码链接是持续定位入口，精确行号只适用于文档所列审计基线；运行状态按第 12 节的证据等级判断。

### 3.1 宿主、配置、事件与队列

| 文件 | 责任、调用入口及输出 |
|---|---|
| [main.py](../plugin/main.py) | `HiganPlugin`：AstrBot 注册、生命周期、事件准入、scope 合批、上游模型配置、工具 schema/dispatch、模型循环集成、媒体与回执、成员观察和调度装配。它仍是大装配入口，不应写成已彻底解耦。 |
| [runtime_paths.py](../plugin/runtime_paths.py#L7-L22) | 唯一 `data_root()`；明确绝对 `HIGAN_ROOT`，否则当前账号 home/higan；不自动创建、迁移或接管别人目录。 |
| [runtime_settings.py](../plugin/runtime_settings.py#L8-L132) | 外部 schema_version=1 设置、owners、persona、成员观察、OneBot、VM bridge、tool_profile。缺失与错误配置分开处理。 |
| [local_runtime.py](../plugin/local_runtime.py#L118-L243) | 同插件 root-only Linux Unix socket；内核 `SO_PEERCRED` 验 UID0 才读字节，固定本地 route；仅 health/history/run，不允许 RPC 自选身份、scope或QQ目标。 |
| [message_batch.py](../plugin/message_batch.py#L15-L33) | `PendingMessage`、真实 sender/message/mentions/quote/native evidence 记录与 `collect_batch`；scope 等锁时合批，到执行时冻结本轮输入。恢复记录仅接受经过校验的RestoredEvent类型，保留原生来源而非信任任意属性。 |
| [intake_batch.py](../plugin/intake_batch.py) | 按实际system/schema/当前消息上下文长度二分最大完整有序前缀，未选输入仍排队；单条超大明确archive-only，不截正文。 |
| [pending_inbox.py](../plugin/pending_inbox.py) | SQLite持久原生输入、exact scope/platform/account/native ID幂等、原子claim与状态终结、启动running→uncertain；只有pending可恢复，不自动重放未知外部效果。 |
| [inbox_runtime.py](../plugin/inbox_runtime.py) | main的唯一持久队列装配：真实账号输入触发恢复、当前策略复核前保留来源、执行前claim、完成/拒绝清理映射；瞬时WebChat/local不伪造恢复能力。 |
| [group_policy.py](../plugin/group_policy.py#L83-L178) | `groups.json` 规范化读写：muted/mention_only/probability/poll；坏文件 fail closed，原子私有临时文件+fsync；线程内共享锁，不是跨进程全局锁。 |
| [task_event.py](../plugin/task_event.py#L5-L118) | 持久任务适配成非人类事件；严格保留已保存 origin/scope，复用 AstrBot OneBot serializer，不能冒充 owner；`_TaskAPI` 只拆已确认 envelope。 |
| [autonomous_turn.py](../plugin/autonomous_turn.py#L15-L49) | `AutonomyEvent` + `run_turn`；内部机会不是用户命令，不伪造 sender/@，复查策略与已观察 origin，使用同一执行锁和发布边界。 |

### 3.2 输入、来源、上下文与记忆

| 文件 | 责任与状态 |
|---|---|
| [attachment_sources.py](../plugin/attachment_sources.py#L1-L23) | 收集真实图像引用与发布者/消息来源；图像发布者不等于图中人物或版权所有人。 |
| [nontext_context.py](../plugin/nontext_context.py#L1-L29) | 文件、语音、视频、表情、卡片和 adapter 未知段落的结构化描述；未知字段保持数据属性，不补造空白人话。 |
| [forward_context.py](../plugin/forward_context.py#L108-L170) | 解析转发卡标识，调用事件原有 OneBot 连接展开节点和嵌套记录；保留作者/消息/media 来源，失败仍是识别到但未读出的来源。 |
| [image_transcript.py](../plugin/image_transcript.py#L1-L11) | 冻结初始图片，工具结果之后追加图片观察，不打断 assistant/tool_call_id 配对。 |
| [conversation_context.py](../plugin/conversation_context.py#L1-L16) | 结构化 context builder + system protocol；人格与不可信历史/观察分层。通过 `memory.context_summary` 独立字段注入摘要，仍是非权威数据。 |
| [conversation_state.py](../plugin/conversation_state.py#L1-L62) | 从已供给证据生成无正文的时序/来源状态；不靠语义猜测权限或“必须回复”。 |
| [history.py](../plugin/history.py#L1-L10) | `Recorder`按scope JSONL追加预览与完整record；checked+flush/fsync明确回执，原生ID按scope/kind/source幂等、坏档案拒写且不覆盖，进程内锁/缓存。主循环读完整档案按长度选；不按条数清理，不声称多进程JSONL事务。 |
| [history_window.py](../plugin/history_window.py) | 内存历史 append/snapshot 默认按序列化长度，不按消息数；显式旧 capacity/minimum 参数仅兼容非主链路调用，实际聊天传 max_chars。 |
| [cross_scope_memory.py](../plugin/cross_scope_memory.py) | 公共群历史的只读共享视图、显式可见性、跨群检索、来源去重/排序；私聊不能并入公共群池。实际群聊走 max_chars 路径，完整取合格归档再按长度选择；旧 limit/count API 仅兼容显式检索/探针，不再驱动聊天窗口。 |
| [memory.py](../plugin/memory.py#L69-L100) | schema v1 JSON 的 `Memory`，全局事实/按scope摘要、精确ID、source/categories/images/structure和checked持久化；兼容导出唯一 WakePersonFacts；摘要使用 checked=True 保存且失败回滚内存，正式调用由 context_summary.prepare 负责。 |
| [fact_context.py](../plugin/fact_context.py#L1-L66) | 每轮可见事实投影为 untrusted memory data；`instruction/preference` 是描述类型，不是系统级授权。 |
| [fact_structure.py](../plugin/fact_structure.py#L1-L28) | 验证事实类型/状态/条件等结构：kind 包括 unspecified/fact/preference/instruction/note，status active/superseded。 |
| [fact_provenance.py](../plugin/fact_provenance.py#L1-L5) | `memory_source` 仅绑定当前真实消息/选中message ID；模型归纳文本不能伪造“全员同意”。 |
| [fact_images.py](../plugin/fact_images.py#L1-L30) | 验证可持久化图片引用与metadata，不联网取图或decode像素；单图大小上限4MiB。 |
| [fact_image_tools.py](../plugin/fact_image_tools.py#L1-L6) | 显式选图经已有视觉converter快照，不自动把所有附件记为事实。 |
| [fact_tools.py](../plugin/fact_tools.py#L1-L28) | scoped分类/查询与精确删除工具；全量目标先验证可见性，写入需可信调用方授权。 |
| [slang.py](../plugin/slang.py#L1-L25) | 独立俚语JSON学习/查询/最近视图，上限800词；当前 plugin 主调用链没有查到调用者，属于可复用但未接线的支线，不可写“自动黑话学习已启用”。 |
| [context_budget.py](../plugin/context_budget.py)、[context_summary.py](../plugin/context_summary.py) | 统一序列化长度预算、最新完整记录后缀、无工具增量摘要、哈希游标、超长记录显式分段、checked 落盘与下轮注入；不删除归档、不把模型摘要升为权威。完整算法与配置见第 13 节。 |
| [wake_persons.py](../plugin/wake_persons.py) | `WakePersonFacts` 唯一存储实现：真实唤醒人入口自动摄取、observed_message/model_fact 分离、当前 scope 可见性、原生 ID 幂等、原子保存、损坏拒写；取消按每人 200 条淘汰。详见第 13 节。 |

### 3.3 模型循环、工具目录、输出与遥测

| 文件 | 责任与证据边界 |
|---|---|
| [agent_loop.py](../plugin/agent_loop.py#L88-L230) | 唯一注入式循环：一份累计 native transcript；规范call ID与JSON参数；逐项调用工具、配对result、保存观察；模型可无工具结束；正数round limit才强制一次无工具最终化。失败抛 `ModelCompletionUnavailable`，不伪造成功回复。 |
| [model_transport.py](../plugin/model_transport.py#L1-L5) | 从provider source解析有效request timeout，主模型HTTP请求复用该默认/配置。 |
| [model_output.py](../plugin/model_output.py#L1-L24) | 解析公开文本/tool calls；剥离约定的私有think/reasoning通道，不将内部推理直接当公开文本。 |
| [model_usage.py](../plugin/model_usage.py#L1-L37) | usage/token数字遥测，不把密钥、prompt和模型全文写日志。 |
| [prefix_metrics.py](../plugin/prefix_metrics.py#L26-L58) | 实际序列化request前缀/缓存候选指标及请求关联；前缀相同不是提供方真实cache命中的证明。 |
| [tool_catalog.py](../plugin/tool_catalog.py#L1-L6) | 统一中性描述与按name去重；schema builder/dispatch仍拥有真实参数与能力，目录不实施工具。 |
| [tool_discovery.py](../plugin/tool_discovery.py#L175-L307) | tool_search/tool_describe + 独立 skills root 的list/read/save；保存需can_write与已有hash，拒绝link/junction；skill文本始终是不可信参考。 |
| [tool_profile.py](../plugin/tool_profile.py#L1-L13) | full/light按轮冻结暴露schema，light可动态发现/描述；不改变权限、后端或能力开关。 |
| [tool_context.py](../plugin/tool_context.py#L25-L49) | 中央redact与有界工具观察档案；SHA256 scope文件名、JSONL、原子compact、id幂等；不是事实自动晋升通道。 |
| [reply_selection.py](../plugin/reply_selection.py#L1-L19) | 静默、reply_to、quote_id、delivery disposition的纯解码与来源校验；不允许凭显示文字猜native消息号。 |
| [final_response.py](../plugin/final_response.py#L13-L21) | 公开最终输出必须结构化 envelope或静默；无工具raw draft至多一次可信repair；不重放工具、不回退发送私有草稿。 |
| [final_publication.py](../plugin/final_publication.py#L1-L22) | 已有公开delivery后的final路由/显式new publication；不按语义相似度判断“可能重复”。 |
| [interim_reply.py](../plugin/interim_reply.py#L1-L28) | `reply_now` 模型自主中途公开发送，调用宿主sender；`finish`仅能在可信完成判据下结束本轮，不等于任意tool输出可控制loop。 |

### 3.4 任务、自主观察与委派

| 文件 | 责任与约束 |
|---|---|
| [tasks.py](../plugin/tasks.py#L1-L11) | tasks.json唯一存储/排程/认领/mark；atomic replace，线程RLock+POSIX sidecar flock；due只是候选，claim才可执行；running claim不自动过期重试，失败暂停待检查。 |
| [task_delivery.py](../plugin/task_delivery.py#L1-L11) | outbound工具attempt/confirmed/failed/unknown的typed证据；判定任务active、claim有效和delivery completion failure，不能靠模型“发好了”认定成功。 |
| [task_planning.py](../plugin/task_planning.py#L1-L5) | 持久任务plan read/update，任务ID/scope/expected revision校验；plan不是独立执行器。 |
| [autonomy.py](../plugin/autonomy.py#L53-L107) | 保存已观察origin、机会时间/轮询/next check/去重通知；默认600秒机会、enabled=true但无已观察origin不凭空创建目标。机会不是自动发送。 |
| [activity_context.py](../plugin/activity_context.py#L1-L24) | 只读任务/worker/skill/error状态摘要供自主轮次；不从状态文本授予权限或伪造待办。 |
| [delegation.py](../plugin/delegation.py#L74-L82) | 实际async worker生命周期、消息inbox、scope hash私有receipt、owned取消；重载可保留同module registry，孤儿/未attach记录不自动恢复或重跑。 |
| [delegated_runner.py](../plugin/delegated_runner.py#L17-L23) | 共用agent_loop的只读研究适配器，明确白名单，无发送/改记忆/VM执行；独立transcript与schema，inbox只在model call前poll，结果通知成为后续机会而非强制发送。 |

### 3.5 QQ读取、社交、传送与回执

| 文件 | 责任与限制 |
|---|---|
| [qq_delivery.py](../plugin/qq_delivery.py#L28-L107) | 构造group/private/temp原生消息数组，literal文本+结构化At；确认status=ok、retcode=0及正消息号才delivered，不重试未知发送。 |
| [qq_native_actions.py](../plugin/qq_native_actions.py#L40-L83) | 实际登录身份查回、仅撤回自身近期群消息；媒体extension分类；文件upload acceptance与message-ID delivery区分。 |
| [qq_social.py](../plugin/qq_social.py#L22-L40) | 账号资料/状态、签名、Qzone、profile工具；绑定安装过的NapCat契约，不是普适QQ保证；写前durable claim，可回读的签名单独验证，Qzone tid只证明接受。 |
| [qq_request_store.py](../plugin/qq_request_store.py#L57-L88) | 抓原生friend/group request token，不从聊天解析申请；按kind/flag/account/platform唯一，public listing不返回flag。 |
| [qq_requests.py](../plugin/qq_requests.py#L130-L229) | owner-only申请list/decision、能力声明；账号匹配，operation+request双claim，提交前状态unknown；不可用的原生申请能力明确返回unsupported。 |
| [message_transfer_store.py](../plugin/message_transfer_store.py#L63-L123) | scope私有immutable剪贴板，UUID ID，完整payload+fingerprint，scope+fingerprint幂等；猜到clip ID不授予跨scope读取。 |
| [message_transfer.py](../plugin/message_transfer.py#L115-L201) | 先原生读source并核验group/message，再真实转发/复制粘贴；不降级拼纯文本假装转发，使用独立operation journal及destination policy。 |
| [group_inspection.py](../plugin/group_inspection.py#L1-L15) | 注入现有bridge读取群信息和成员；保留实际role，失败不回空列表冒充成功。 |

### 3.6 VM、浏览器、工作区与图片发现

| 文件 | 责任与限制 |
|---|---|
| [workspace_tools.py](../plugin/workspace_tools.py#L1-L7) | read/write/edit/glob/grep/cwd/present/read_image生成标准库脚本，经已有VM executor执行；不是DSH宿主文件系统；需先读+SHA256/CAS才覆写；无delete/move/shell工具，但VM账号本身不是安全沙箱。 |
| [vm_jobs.py](../plugin/vm_jobs.py#L1-L18) | 生成Linux受管后台脚本；jobs状态、owner PID/UID/starttime/pgrp/session/argv验证及终止；不在插件进程直接执行；状态不是永恒可靠，孤儿会lost。 |
| [browser_eval.py](../plugin/browser_eval.py#L1-L12) | 固定JS包装和response解码，弥补VM/CDP丢失exceptionDetails，保留真异常而非假空成功。 |
| [vm_click_gate.py](../plugin/vm_click_gate.py#L1-L27) | 每轮单slot视觉预览/确认ticket、图片已进入model request的证据、hash/expiry/新截图复核；不是用户授权，也不能消除最后截图与真实点击之间race。 |
| [vm_pointer_runtime.py](../plugin/vm_pointer_runtime.py#L1-L21) | 唯一canonical pointer path、时间/尺寸/点数验证及注入runner执行；总点≤128、时长≤10000ms，不是QQ/桌面后台服务。 |
| [vm_pointer_adapter.py](../plugin/vm_pointer_adapter.py#L1-L9) | opt-in POSIX xdotool可信adapter；需宿主显式赋值，无环境自动激活；独占GUI所有权仍需外部保证。 |
| [vm_execution_lease.py](../plugin/vm_execution_lease.py#L1) | 可信gate向adapter传递一次执行新鲜度lease，排队等待后再检查；模型JSON不能创造该能力。 |
| [image_search.py](../plugin/image_search.py#L1-L21) | 百度图片/页面img的公开metadata发现，公共地址验证、redirect验证和响应大小限制；不下载或自动发送候选，须后续显式动作。 |

### 3.7 插件元数据

[metadata.yaml](../plugin/metadata.yaml) 声明AstrBot插件标识和版本。主文件register ID为 `astrbot_plugin_higan`；注册描述与版本不是独立发布/线上状态证据，不能代替当前Git与实际运行哈希。

## 4. 真实调用与数据流

### 4.1 前台消息流

1. AstrBot adapter 提供真实 `AstrMessageEvent`，唯一注册处理器是 `HiganPlugin.on_message`。
2. 若raw post_type=request：捕获到RequestStore并停止事件，不把申请备注当人类指令；若notice只含附件metadata：保存观察/历史但不伪造第二个人类发言。
3. scope：群为实际群号；私聊为 `private:<sender>`；WebChat用真实origin的SHA256构造独立private scope，避免不同聊天窗口混线。本地用固定 `local:operator`，不是伪造QQ号。
4. 结构化非文本/合并转发/引用/附件来源进入本轮证据。可收集接收图片到sticker库；这也意味着启用QQ后，图片收集是实际持久化行为，需要运营者明确数据保留范围。
5. slash输入停止框架默认链，不进入默认模型/问候；常规消息写档案，策略不准入也可保留历史。群策略控制的是处理/发送，不应误认为自动删除或隐私隔离。
6. 可验证的原生QQ先进入SQLite持久pending，同scope/platform/self/native ID幂等；每scope单worker，取得全实例FIFO execution_lock后按实际长度选完整有序前缀、claim成功才进入执行，其余保留待处理。下条真实同账号事件可恢复未开始项；启动running转uncertain而不自动重放。WebChat/local为活请求能力，不承诺崩溃恢复；WebChat逐请求避免错投回复。
7. `_handle_one` 复查策略/概率/interval，进 `_decide_tools_first`。模型是否静默由协议决定，不保证每条消息都回答。
8. 每个工具通过 `_execute_tool`/`_dispatch_tool` 对接原有模块。返回观察写archive并配对进入transcript；截图通常只供模型看，不自动发布。
9. final envelope 经 `decode_reply` / `final_publication`，合法body/引用才进 `_send`。真正产生公开回执后才写assistant_delivery记录。中途 `reply_now` 使用同一sender，不建立第二种发送语义。

主要源码锚点：[main.py](../plugin/main.py) 中 `on_message`、`_drain_pending`、`_handle_one`、`_decide_tools_first`、`_dispatch_tool`、`_send`；纯引擎 [agent_loop.run](../plugin/agent_loop.py#L88-L230)。

### 4.2 模型请求流

- `_her_llm_config` 从 `ASTRBOT_ROOT/data/cmd_config.json` 读取被绑定的provider：优先 `agent_runner.config.model.provider_id`，兼容 `provider_settings.default_provider_id`；然后查 `provider_source_id`。只有单provider/source时才有无歧义旧兼容，不能任意回落某个供应商。
- `_llm_tools`、`_llm_plain` 走OpenAI-compatible `/chat/completions`，不是自动支持所有厂商原生协议。provider base、key、model必须来自用户自己的配置；初始settings不是每轮provider权威。
- `llm.json`仅提供可选 `reasoning_effort`、正 `max_tokens` 等额外生成参数，不应作为主要模型绑定入口。timeout来自source并由 [model_transport](../plugin/model_transport.py) 验证。
- [agent_loop](../plugin/agent_loop.py#L117-L230) 维护append-only transcript，tool call顺序执行而不是对一个batch盲目并行；每个call都配tool result，即使参数不合法或工具失败。MAX_ROUNDS默认None，环境正数限额才有工具轮数天花板；强制final失败不触发再次执行工具。
- native模型故障可调用一次text-only finish；最终协议repair与模型推理结束是不同层次，repair不准改成未经授权的reply target。

### 4.3 任务、主动机会与worker

- 任务每25秒检查due候选，保存origin缺失则暂停；持有与前台同一FIFO锁后才claim。claim ID在mark时再次比较，旧runner不能覆盖已撤销或新认领任务。
- 任务继续调用 `_decide_tools_first`，无当前人类messages；TaskEvent保留原来的destination。任务创建只表示scheduler accepted，绝不表示已执行或已送达。
- `repeat=once`成功才done；`every`默认after_completion，fixed_rate保原相位且跳过错过轮次。MIN_INTERVAL=30秒、MAX_TASKS=50，默认interval=120、script检查=60、script timeout=20秒；不是准点保证。
- running认领不自动过期；失败paused，避免崩溃后不确定副作用被重复执行。需要人工审查恢复，不把可用性优先于防重安全。
- 主动controller每30秒被poll，默认heartbeat机会600秒；muted/mention_only仍阻止主动发言，需真实observed origin。前台排队/锁busy时defer，机会未确认前不消耗；实际轮次开始前ack，所以未知副作用也不被自动重放。
- `agent_start`启动真实async研究worker，用READ_ONLY_TOOLS而非工具名前缀推测；worker可以检索可见事实/历史、网页、workspace只读、skills参考，无QQ发送、事实写入、VM exec。completed只生成去重的 `delegated_result` 机会，父轮次自己决定是否发送。

锚点：[tasks](../plugin/tasks.py#L158-L223)、[AutonomyController](../plugin/autonomy.py#L129-L174)、[run_turn](../plugin/autonomous_turn.py#L41-L83)、[Manager](../plugin/delegation.py#L130-L183)、[worker边界](../plugin/delegated_runner.py#L69-L98)。

### 4.4 现有QQ通路与三类“成功”

- message ingress走AstrBot平台adapter；工具native actions主要走 `_onebot_http(action, params)`，用显式OneBot settings/token，完整action path保留envelope，不把根欢迎页当action成功。
- `_send`通过真实event serializer发送媒体和引用；TaskEvent需校验origin三段与scope一致，然后调用native send。`qq_send`走 [qq_delivery.build/send](../plugin/qq_delivery.py#L28-L107)。
- 普通消息：`status=ok`、`retcode=0`、正 `message_id` 才可标记native_confirmed；仍不是“所有成员已读”。
- 普通文件upload：原生可合法返回null，故只标记accepted / native_upload_acknowledged，不伪造message_id或delivered。
- Qzone tid：接受发布请求，不代表公开页回读成功；签名有额外readback matched/mismatch/unknown。申请接受：membership仍unknown，不能把accept acknowledgement写成好友/群成员已形成。
- 各写操作避免未知结果自动retry。社交、申请、转发有durable journal；普通 `qq_send`不因此自动得到跨重启的operation dedup保证。

### 4.5 VM与文件通路

`_dispatch_tool` → `_vm_tool` → 选定 `HIGAN_VM_BRIDGE_BASE` 的固定 `/vm/...` route。workspace/managed jobs只生成标准库脚本，经vm_exec执行；不是在本地仓库或DSH中执行用户脚本。

公开仓库在此范围提供**客户端和协议，不提供该VM bridge、浏览器、邮箱账号服务**。默认loopback18003只是回退地址，不是服务存在证明；Docker容器内loopback也不是宿主loopback。fresh compose没有额外VM、mail或browser服务。

点选必须先preview、实际把图放入model request、再confirm同ticket/hash/坐标并复取新截图；路径preview和confirm共用单slot。路径执行器默认未安装，确认返回backend_unavailable。直接drag/slider_drag已拒绝，vm_exec/eval是可信通用能力，并没有因此成为沙箱。

## 5. bot_core完整模块清单

| 文件 | 现在已经实现什么 | 不能据此宣称什么 |
|---|---|---|
| [__init__.py](../bot_core/__init__.py) | 包导出边界。 | 不会加载QQ或完整配置。 |
| [contracts.py](../bot_core/contracts.py#L58-L141) | immutable Endpoint/Principal/InboundEvent，scope:v1 JSON key，platform+bot account命名空间及一致性验证。 | constructor不是认证；该prototype未替换plugin现有scope/事件/存储，不是迁移已完成。 |
| [harness.py](../bot_core/harness.py#L41-L106) | 注入system、llm(_messages)、run_tool、callback和锁，复用唯一agent_loop；返回真实text/used_tools/深拷贝transcript。 | 无记忆、persona/凭据读取、QQ工具安装、进程恢复或框架host。 |
| [http_host.py](../bot_core/http_host.py#L30-L113) | 标准库text-only OpenAI HTTP adapter；HTTPS或明确loopback HTTP，拒绝URL内凭据和redirect；response≤8MiB，strict JSON。 | 不注册完整工具、不具QQ/图片/厂商原生协议能力。 |
| [__main__.py](../bot_core/__main__.py#L43-L91) | 显式CLI，可信MODULE:FACTORY或HTTP配置，key用env名；输出published=false、destination=local。 | standalone输出不是QQ发送；`--host`是可信operator code import，不是安全插件沙箱。 |

## 6. qq_transport完整模块与未完成状态

### 6.1 Python机制已编写，所有handler都要显式注入

| 文件 | 机制与schema |
|---|---|
| [__init__.py](../qq_transport/__init__.py) | 无隐式service/默认handler安装。 |
| [store.py](../qq_transport/store.py#L100-L170) | OperationJournal：`operations(account, operation_id, action, fingerprint, state pending/complete, response, created_at, updated_at)`，复合主键(account,operation_id)，canonical params只存SHA256；pending永不自动过期。 |
| [actions.py](../qq_transport/actions.py#L59-L76) | ActionDispatcher + Handler mutation声明，严格account/action/params/echo验证，mutation需operation ID；不支持动作不转别的backend。 |
| [events.py](../qq_transport/events.py#L16-L40) | `transport_events(sequence,account,event_id,fingerprint,payload)`，unique(account,event_id)；`event_cursors(account,consumer,sequence)`；immutable outbox、顺序ack。 |
| [http_api.py](../qq_transport/http_api.py#L29-L50) | 显式start的loopback HTTP `POST /action` + Bearer + X-Operation-Id；account固定配置，bounded framing/JSON，close排空。不是QR/login服务。 |

**语义**：操作claim之后最多执行一次handler，崩溃可能永远pending；这是防重复而非保证一定执行。event send后ack前崩溃可重复投递，sink必须按稳定event_id去重；多进程共享同consumer无分布式claim。event pump不是reverse WebSocket，发送字节不是应用ack。[README边界](../qq_transport/README.md#L32-L55)。

### 6.2 JS native生命周期（dormant、单次、可信宿主注入）

| 文件 | 已有构件 | 实际边界 |
|---|---|---|
| [native_metadata.js](../qq_transport/native_metadata.js#L117) | 显式manifest/version/arch/appid与data path解析、冻结config。 | lexical path/metadata不证明文件存在、ABI匹配或身份有效，不合成账号数据。 |
| [native_host.js](../qq_transport/native_host.js#L45-L163) | 准备engine/login/startup/session factory身份，startup/legacy显式分支，缓存handle，单次prepare。 | 不加载QQ、不初始化/连接/启动会话；native尝试后可能必须process exit，dispose逻辑释放不等于原生清理。 |
| [native_login.js](../qq_transport/native_login.js#L163-L172) | 显式initialize/connect/request QR、监听状态与callback身份、严格二维码payload、固定诊断。 | constructor不加载native，owner供应matching wrapper/config；方法返回不证明connected/authenticated，必须真实callback。独立登录链尚未验收完成。 |
| [native_session.js](../qq_transport/native_session.js#L112-L147) | 显式session initialize/start与ready callback，identity绑定、prepared handle一致性。 | 无配置builder/账号合成/native自动加载；不可跳过authenticated identity。 |
| [native_messages.js](../qq_transport/native_messages.js#L122-L142) | 有限text element投影、受控listener、消息send callback/timeout、不确定state。 | 不覆盖所有native消息类型与QQ动作，未知raw blob不透传；不是全量OneBot实现。 |
| [native_backend.js](../qq_transport/native_backend.js#L109-L203) | 组合上述controller，login→session→ready→messaging→send的显式生命周期，QR私有订阅与普通telemetry分离。 | [快照](../qq_transport/native_backend.js#L221-L232) 永远 `independent_backend_ready:false`；有限send_path_ready不等于完整独立后端、全功能对齐或上线许可。 |

**明确未完成**：真实QQnative全动作handler、完整独立登录、生产事件ingress、reverse WebSocket/reconnect/ack、全媒体/社交/文件兼容、业务端到端送达、状态对账/迁移、加密/retention/多机安全。现有动作声明是180基础+360别名覆盖清单，不是540个实测可用实现；Python core无真实native handler自动注册。[明确限制](../qq_transport/README.md#L46-L67)。

## 7. 真实权限与信任边界

### 7.1 身份与授权来源

- owner只来自已配置QQ ID或同实例内核验证的LocalEvent capability。昵称、prompt里的“我是管理员”、quoted source、skill、history、tool result、TaskEvent/AutonomyEvent都不能构造该能力。[local event认证](../plugin/local_runtime.py#L33-L40)、[task sender](../plugin/task_event.py#L15-L25)。
- 本地socket要求Linux root、root-owned data root和严格0700 run目录/0600socket；先SO_PEERCRED再JSON。接受后的工作与客户端断开分离，输出仅local_recorded，client_received=false，不伪造QQ回执。
- QQ request工具除capabilities外，**owner-only且先门禁再读store**，AutonomyEvent也不获得批准申请权限。[request dispatch](../plugin/qq_requests.py#L130-L148)。
- social写、skill_save、autonomy_schedule由 `can_manage`控制：真实owner或typed autonomous turn + authentic origin + policy。Autonomy明确不能改群权限/唤醒名单，也不能发向未观察destination。
- delegated worker仅固定READ_ONLY_TOOLS；tool discovery也只看worker子目录，不能通过发现请求拿到父工具schema后越权dispatch。

### 7.2 不可美化的现状差异

- 普通前台 `group_policy` 在 `_dispatch_tool` 分支直接调用store.update；不能写成“只有owner可设置所有群策略”。Autonomy分支另行禁止mutation，不等于所有人都被同样门禁。
- `task_add`要求真实origin并强制destination=current scope；但task_update/status操作按全局task ID/title走tasks API，没有与plan同样统一scope检查；task_list还能显式传scope。模型不应因看到任意ID就视为有授权，代码现状也需在后续权限审计明确处理。
- workspace和一般VM工具没有普遍owner-only前置门禁。它们受调用入口和VM账号权限约束，**不是沙箱**；`vm_exec`/browser eval具广泛能力。不能把视觉点击ticket描述为整体代码执行权限系统。
- 转发源group callback主要验证事件存在和正数字group，native读取还要核验来源；这是实现策略，不是“所有跨群转发只有owner”。公共群历史是设计为共享的，不适用于私密群默认隔离承诺。
- 部分legacy读取仍best-effort，例如Memory.load与history读取可返回空；Recorder.append现返回明确persisted/duplicate/uncertain回执，append_checked损坏/IO错误会抛受控异常，入口和发送后检查结果。历史写入只承诺进程内锁，不等于跨进程事务；GroupPolicyStore/AutonomousController也是进程内锁。持久pending的SQLite事务只保护自身，不使多个完整Bot可无条件共享全部状态目录。

以上是源代码状态说明，不对生产真实权限或外部服务ACL作保证；本轮未修改这些边界。

## 8. 存储文件、schema与保留边界

### 8.1 fresh安装（外部DataDir）

| 路径（逻辑名） | 结构/来源/用途 | 敏感性与行为 |
|---|---|---|
| `settings.json` | schema_version=1；model={base_url,model,api_key}、owner_qqs、persona、dashboard_password、deployment={web_port,onebot_port}；可选onebot/member_watch_groups/tool_profile。 | 明文模型Key与初始面板密码的私有配置；POSIX新建0600。不提交，不在报告复制值。 |
| `.bootstrap.json` | schema_version=1、runtime_name。 | 管理身份标记；不匹配拒绝docker操作，不盲目接管。 |
| `compose.env` | BOT_DATA_DIR精确路径。 | source路径在仓库外；load_install精确比对防漂移。 |
| `astrbot/.qhunliv-bootstrap.json` | schema_version=1、plugin_sha256。 | 插件镜像内容标记，变化需受控update/migration，不自动覆盖。 |
| `astrbot/cmd_config.json` | 实际AstrBot provider_sources/provider/agent_runner/dashboard/platform等。 | 包含运行模型Key和面板认证数据；初始化后由运行时/面板管理，不由up重复覆盖。 |
| `astrbot/plugins/astrbot_plugin_higan` | 指向镜像bundled plugin symlink。 | 外来目录/不正确link拒绝启动。 |
| `astrbot/data_v4.db` | 上游持久数据库，插件可只读加载persona。 | 不是第一方schema，不应假称全部由Memory维护；原生schema由固定上游定义。 |

### 8.2 HIGAN_ROOT

| 逻辑路径 | 实际数据契约 | 原子性/保留注意 |
|---|---|---|
| `groups.json` | 已知：mention_only/muted列表、poll_minutes/reply_pct map，兼容wake相关extras；群ID正ASCII数值。 | GroupPolicyStore保留extras，私有temp+replace+fsync，进程锁；wake_persons 为按group/scope到QQ列表的配置映射，wake_person_conf 保存按人的回复设置；磁盘观察独立见第13节，不把成员配置与观察库混合。 |
| `memory/memory.json` | version=1、global={summary,facts,next_fact_id等}、scopes={scope:{summary,updated_at,turns…}}；fact有id/text/at/source_scope/source/categories/images/structure。 | checked写原子且fsync；事实保留来源与旧未知标签兼容。摘要 scope 块增加 context_compaction 的 covered_rows/covered_sha256/partial_chars/partial_sha256；仅长度溢出触发，计数仅作精确续写游标不是保留上限。 |
| `memory/wake_persons.json` | 每人事实/观察的独立持久库，脱离global facts。 | version=2，persons[QQ] 为记录数组；新增 kind/source/稳定 w-ID。真实消息摄取在回复概率前；原子保存/损坏拒写/同进程锁，旧未知来源不伪造 scope。 |
| `history/<sanitized-scope>.jsonl` | 每行ts、line、scope、可选完整record；旧行无scope保留；数字group文件用于群发现，private scope被sanitize为filename。 | flush/fsync确认后返回成功，原生ID按精确scope/kind/source幂等，坏档案拒写不覆盖；line预览最多2000字符、record完整，不按条数删。sanitized key截断仍可能碰撞，不能推广通用账户隔离。 |
| `pending_inbox.sqlite3` | 原生QQ输入、平台/账号/原生ID、投影附件与pending/running/uncertain/completed/failed/blocked状态。 | SQLite EXTRA同步/原子claim，未开始可恢复，启动未知执行隔离不重放；校验scope/account后用现有TaskEvent发送器。不存角色/凭据/raw payload；2MiB整项拒绝，无自动清理，终态保留防重。checksum只防损坏不防有本地写权限的攻击者。 |
| `tool_observations/<sha256-scope>.jsonl` | id/timestamp/status/question/tool_name/arguments/result，有界JSON及redact。 | 默认每scope100记录；结果4000、prompt16000、record12000字符；线程锁+原子compact，非事实库。 |
| `tasks/tasks.json`、`.lock` | JSON任务数组：id/title/goal/brief/resume_prompt/scope/origin/status/interval/repeat/cadence/deadline/execution_state/claim/plan/outcome。 | POSIX跨进程flock；Windows仅线程锁；terminal可在总量压力回收，但running claim不回收重试。 |
| `autonomy.json` | enabled/interval_seconds/scopes/origins/last_run/poll_last/wake_slots/next_checks/pending_events/notifications_seen。 | replace+fsync，进程内锁；seen通知持久去重，不自动把旧机会当新命令。 |
| `delegation/<uuid>.json` | hashed scope、redacted task/label、status、timestamps、owner_pid、boot_id、inbox、result/error。 | 0600 atomic replace；result上限10000字符，receipt不是可执行恢复数据。 |
| `qq_requests/requests.sqlite` | qq_requests：request_id PK、kind/flag/user/group/sub_type/comment/time/status/outcome/origin/account/platform及unique(kind,flag,account_key,platform_key)。 | 原始flag私有，只resolve给可信handler；FULL同步，listing无flag；不是QQ全部native backlog。 |
| `social.sqlite3`、`qq_requests/operations.sqlite`、`message_transfer/operations.sqlite` | 复用social_operations(operation_id PK,payload_hash,state,receipt)，write前claim。 | WAL/FULL同步，pending/unknown不自动重试；operation参数只hash，receipt净化；数据库及WAL仍应私有。 |
| `message_transfer/clipboard.sqlite` | message_clipboards(clip_id PK,scope,fingerprint,payload)，unique(scope,fingerprint)。 | private 0600，FULL同步；完整native message/media URL可能敏感；无自动跨scope共享或已实现retention承诺。 |
| `llm.json` | 可选额外生成参数reasoning_effort/max_tokens。 | 不是runtime provider绑定唯一权威；不要将旧内容复制到文档。 |
| `skills/<name>.md` | 可选首行skill-metadata JSON注释+UTF8参考正文，SHA256支持CAS。 | 拒绝root/ancestor links和junction，skill_save需权限；参考文本不是可执行system授权。 |
| `run/local.sock` | 一行JSON RPC，health/history/run。 | tmpfs+root peer，16KiB request/2秒read，不用于QQ身份；socket不是持久库。 |
| `workspace.json`、`workspace.lock`、`workspace/` | **VM端** cwd与observations hash记录；file工具在VM侧读取/更新。 | 先读证明+CAS，不防不合作进程同时改文件；不是主机workspace沙箱。 |
| `jobs/<id>/` | **VM端** runner/script/status.json/owner.json/stdout等。 | 进程UID/birth/session/argv校验，输出6000、日志65536，私有目录；不是不受限日志系统。 |
| `images/` | 归档/VM截图/视觉checkpoint图片。 | preview gate只接受配置root直接子PNG；媒体含用户内容，需运营者另定retention。 |

### 8.3 AstrBot相对工作目录的媒体

`data/stickers/manifest.json`、sticker二进制、`data/temp/`、`data/downloads/`、`data/higan_avatar.json`由 [main媒体逻辑](../plugin/main.py) 使用。manifest含file/desc/used等索引，最多300张、接收单张4MiB，下载图5MiB；动图视觉最多4帧，模型request最多3图、指代近期最多5图。移出sticker索引不删原文件；实际磁盘增长不能只按manifest数量估算。

### 8.4 独立transport与Lite状态

- transport SQLite路径必须由调用者显式传入专用私有目录，绝不能指向QQ账号session或生产Bot库；operations只hash params，但response/events payload仍可能含私密内容。[journal](../qq_transport/store.py#L107-L127)、[outbox schema](../qq_transport/events.py#L19-L40)。
- 原生Lite默认当前用户LOCALAPPDATA/QhunlivBot/Lite（非Windows则home下独立路径），settings有application marker/schema/base/model/system及DPAPI ciphertext或env名；不保存明文key、不保存磁盘聊天历史。[Lite配置](../tools/lite_native.py#L24-L65)。

## 9. 配置键、默认值与优先级

| 键/位置 | 默认/规则 | 生效边界 |
|---|---|---|
| `HIGAN_ROOT` | 若给出必须非空绝对路径；否则Path.home()/higan。 | 不自动采用历史开发机或他人账号路径；见runtime_paths。 |
| `HIGAN_SETTINGS_FILE` | 缺省无外部settings；若有仅该path，schema_version严格1。 | 不扫描配置或凭据；运行时按需读取。 |
| `owner_qqs` / `HIGAN_OWNER_QQS` | 默认空，即没有预设个人管理员。 | settings存在优先，即使空也不回退env；standalone env为JSON数字字符串列表。 |
| `persona` | fresh默认空；无settings时可兼容只读AstrBot persona DB。 | 不预置个人身份，不从名字推权限。 |
| `member_watch_groups` / `HIGAN_MEMBER_WATCH_GROUPS` | 默认空=不轮询成员，非“监听全部”。 | 已配置群60秒检查；与TARGET_GROUPS回复范围分开。 |
| `tool_profile.mode/initial_tools` | full / []；可选light。 | 每轮冻结，仅schema暴露；light默认discovery+reply_now，仍需原权限。 |
| `onebot.enabled/http_base/token` | settings里默认enabled=false。 | settings存在且disabled时不回退旧local地址；无settings可用HIGAN_ONEBOT_BASE/TOKEN及caller fallback。 |
| `HIGAN_ONEBOT_BASE/TOKEN` | 明确standalone配置；main有旧local fallback13000。 | 地址不是登录或就绪证明；不能把旧兼容端口当fresh QQ已启动。 |
| `HIGAN_VM_BRIDGE_BASE` | loopback18003。 | 仅endpoint，未安装外部服务；容器内namespace需单独规划。 |
| `ASTRBOT_ROOT` | 容器/AstrBot；standalone caller当前目录兼容。 | provider config定位到root/data/cmd_config.json。 |
| `HIGAN_TOOL_ROUNDS` | 字符串0→None，默认模型自主结束。 | 正数才限制工具轮；非合法数字会初始化失败，不能传任意文本。 |
| `HIGAN_TOOL_TURN_TIMEOUT` | 0→None。 | 可选整轮超时，和单provider request timeout不同；未知副作用不因此可自动retry。 |
| provider_sources[].timeout | bootstrap初始120秒；未配置时CHAT_TIMEOUT=45兼容。 | 正数/有限值检查见model_transport；不是所有工具超时。 |
| `llm.json.reasoning_effort/max_tokens` | 无文件则不加；max_tokens需正数。 | 模型额外参数，非默认提供方或上下文容量保证。 |
| `deployment.web_port/onebot_port` | 6185/6199，不同1..65535整数。 | fresh默认冲突可选空闲值并保存；显式env不被悄悄改写。 |
| `BOT_WEB_PORT/BOT_ONEBOT_PORT` | 可暂时覆盖已保存端口。 | 不修改原settings，不证明服务活跃；info与Compose共用同一来源。 |
| `BOT_DATA_DIR` | bootstrap固定所选外部root。 | compose bind paths；create_host_path=false，必须先init。 |
| `BOT_MODEL_BASE_URL/BOT_MODEL/BOT_MODEL_API_KEY` | 独立bot_core/Lite CLI明确env，无预设provider。 | 不属于完整AstrBot每轮配置；key参数只能是env名。 |

**本轮新增配置 `context_memory.max_context_chars` 默认600000序列化Unicode字符；扣除固定请求成本后的聊天区60%保留原文、40%摘要/归档，唤醒观察不设条数淘汰。配置片段与边界详见13.4，不沿用旧条数参数冒充长度。**

## 10. tools完整清单（按执行层和副作用分组）

工具文件不是运行服务。这里逐项登记全部非缓存第一方代码；本文没有执行任何runtime probe、native实验或配置写工具。

### 10.1 安装、配置、Lite和基础验证

| 文件 | 责任/副作用 |
|---|---|
| [bootstrap.py](../tools/bootstrap.py) | fresh init/doctor/up/wizard/info/stop/status/logs/health/chat；up建镜像容器、stop只选中项目，chat可能计费/执行获授权工具。 |
| [setup_inputs.py](../tools/setup_inputs.py) | 模型地址/名称/Key和owner输入校验、隐藏key提示；不探提供方。 |
| [setup_ports.py](../tools/setup_ports.py) | fresh端口冲突检测并短时持有；仅未显式选择的默认值可换空闲端口。 |
| [setup_wizard.py](../tools/setup_wizard.py)、[setup_wizard.html](../tools/setup_wizard.html) | full setup固定本地页面+loopback HTTP controller；精确Host/Origin/Bearer/CSP，save/start/stop/chat/credential显式动作；raw logs不传HTTP。 |
| [windows.ps1](../tools/windows.ps1) | WSL发行版/用户/参数验证、argv转发、授权URL marker消费；不接管WSL默认用户，Windows env Key不自动转Linux。 |
| [windows-setup.ps1](../tools/windows-setup.ps1) | 双击环境编排，询问WSL/组件安装，复用bootstrap；不自动强制重启、不替换现有Docker。 |
| [windows-setup-ui.ps1](../tools/windows-setup-ui.ps1) | Windows图形欢迎、确认、发行版选择与固定错误提示；不是第二套安装核心。 |
| [windows-prerequisites.sh](../tools/windows-prerequisites.sh) | check/install显式模式；可从已有签名Linux源补Python/Docker/Compose并启动Docker，需用户允许；不是只读脚本。 |
| [windows-lite.ps1](../tools/windows-lite.ps1) | 找现有Windows Python≥3.10、固定命令选项与pipeline，转native Lite；不自动装Python。 |
| [lite_native.py](../tools/lite_native.py) | 独立外部settings、configure/info/chat/web；当前用户DPAPI或env Key，fresh目录防接管。 |
| [lite_credentials.py](../tools/lite_credentials.py) | Windows当前用户CryptProtectData/UnprotectData；不是跨账号可迁移密钥库。 |
| [lite.py](../tools/lite.py) | portable CLI与内存文字conversation，复用ChatCompletionHost；无工具/持久历史。 |
| [lite_web.py](../tools/lite_web.py) | stdlib loopback浏览器聊天，一份非持久conversation；有界body与同源授权，不是完整AstrBot面板。 |
| [build_lite.py](../tools/build_lite.py) | 按源码allowlist建新ZIP；目标须仓库外且不存在，不下载/打包配置/解释器。 |
| [local_runtime_client.py](../tools/local_runtime_client.py) | root-local JSONL Unix client health/run/history；无模型配置或QQ登录。 |
| [validate_release.py](../tools/validate_release.py#L11-L35) | plugin/tools payload AST语法与凭据pattern扫描，不读private runtime；未覆盖bot_core/deploy/所有JS正确性，不能替代整仓测试。 |
| [test_linux_launcher.py](../tools/test_linux_launcher.py#L1-L15) | stdlib unittest执行已有Linux launcher fixture，不部署。 |
| [command_toggle.py](../tools/command_toggle.py) | 通过native hot command API禁用/恢复一个审核命令，改变运行配置。 |
| [set_group_policy.py](../tools/set_group_policy.py#L9-L33) | 指定plugin/config/group/mode和新backup，复用GroupPolicyStore保存+readback；会写配置，不发chat或重启。 |
| [task_deadline_review.py](../tools/task_deadline_review.py#L15-L50) | 精确ID/旧deadline compare-and-set审核，默认预览，--apply才改idle active任务；仍写指定审计output。 |
| [task_cadence_review.py](../tools/task_cadence_review.py) | 指定快照的任务cadence审核/应用，保留origin/goal，不运行task或重发副作用。 |

### 10.2 模型/行为/上下文探针

| 文件 | 测量对象与边界 |
|---|---|
| [production_probe.py](../tools/production_probe.py) | 真实model/只读工具隔离smoke，不QQ发送或改真实memory；名字不等于本轮已跑生产。 |
| [batch_model_probe.py](../tools/batch_model_probe.py) | 真实model、synthetic batch，所有tool effect禁用。 |
| [model_simulation_audit.py](../tools/model_simulation_audit.py) | 真实model+确定性隔离fixtures的多场景dialogue审计，不外部业务写成功。 |
| [tool_contract_audit.py](../tools/tool_contract_audit.py) | 真实schema/dispatcher离线契约，外部接口模拟；不是网络接口可用性。 |
| [mention_policy_probe.py](../tools/mention_policy_probe.py) | AST提取真实mention admission，离线无模型/工具/发送。 |
| [structured_silence_probe.py](../tools/structured_silence_probe.py) | 结构化后台静默边界，不能代表全部模型都遵守。 |
| [interim_reply_probe.py](../tools/interim_reply_probe.py) | 真实model多段reply_now选择，本地记录delivery而不QQ发。 |
| [reply_finish_probe.py](../tools/reply_finish_probe.py) | 真实model选择reply_now finish，隔离状态、无QQ发送。 |
| [explicit_publication_probe.py](../tools/explicit_publication_probe.py) | native loop与autonomy显式发布，model/platform用记录器；模拟receipt不是送达。 |
| [message_transfer_probe.py](../tools/message_transfer_probe.py) | 真实分发/剪贴板/防重，platform recorder代替真实转发。 |
| [fact_queue_probe.py](../tools/fact_queue_probe.py) | 临时事实库、只读worker、claim/合批锁离线，无网络/model或真实memory改动。 |
| [instruction_provenance_probe.py](../tools/instruction_provenance_probe.py) | 真实model对synthetic条件历史/当前trigger区分；tool effect禁用，不替代大context验收。 |
| [attachment_read_probe.py](../tools/attachment_read_probe.py) | 图片发布者归属进入真实model的验证，不改persona或平台发送。 |
| [forward_read_probe.py](../tools/forward_read_probe.py) | 指定实际forward来源经解析到真实model，不QQ发送。 |
| [nontext_read_probe.py](../tools/nontext_read_probe.py) | 实际非文本source到真实model，不QQ发送。 |
| [file_source_probe.py](../tools/file_source_probe.py) | 文件metadata到model，不下载或执行文件内容。 |
| [autonomy_read_probe.py](../tools/autonomy_read_probe.py) | 部署模块的真实model/只读QQ工具，无公开effect。 |
| [capability_probe.py](../tools/capability_probe.py) | 页面读取、故意JS异常、隔离scheduler与无害long job；会触外部VM/创建验证job，不是全只读。 |
| [dsh_capability_probe.py](../tools/dsh_capability_probe.py) | VM filesystem+隔离真实model worker；不QQ写，但VM文件/worker可能有状态。 |
| [image_search_probe.py](../tools/image_search_probe.py) | 真实网络图片metadata发现，不发送候选。 |
| [cache_prefix_probe.py](../tools/cache_prefix_probe.py) | synthetic matched滑窗的真实model/cache比较。 |
| [history_cache_probe.py](../tools/history_cache_probe.py) | timing-last vs aligned histories的真实usage比较。 |
| [shared_history_cache_probe.py](../tools/shared_history_cache_probe.py) | 默认离线序列化前缀对照，显式--live才真实model四请求；无工具/QQ发送。 |
| [context_retention_probe.py](../tools/context_retention_probe.py) | 完全离线合成饱和输入，量化旧策略与60/40长度分区原文保留、转摘要/归档长度和自动删除数；不读运行状态、不联网。 |
| [prompt_structure_cache_probe.py](../tools/prompt_structure_cache_probe.py) | metadata-only lossless layout对照，默认离线，--live四请求。 |
| [cache_layout_probe.py](../tools/cache_layout_probe.py) | 显式data-root读取真实历史/观察+真实model四请求，无QQ/tool effect；输出含敏感性边界，不能读取未授权目录。 |

### 10.3 QQ静态盘点、隔离运行与native研究（未接生产）

这些文件集中到共享jail/runner，不能因名字有“control/probe”就认为无副作用或可任意执行。`--execute`、Linux root、命名空间/chroot/pivot/private-X/bus、降权、watchdog与resource limits是实验前提，不是“默认允许登录”的授权。本文没有运行。

| 文件 | 固定职责 |
|---|---|
| [qq_transport_parity_audit.py](../tools/qq_transport_parity_audit.py) | 对required manifest/installed inventory做声明coverage比对，不能证明native可用。 |
| [qq_native_preflight.py](../tools/qq_native_preflight.py) | 显式qq-root软件静态检查，无账号native执行；固定not-ready。 |
| [qq_asar_inventory.py](../tools/qq_asar_inventory.py) | bounded ASAR header/offset metadata，不读取entry payload、不解包/执行。 |
| [qq_native_binding_inventory.py](../tools/qq_native_binding_inventory.py) | pristine ELF/readelf元数据，不加载binding或账号状态，ABI未验证。 |
| [qq_isolated_runtime_probe.py](../tools/qq_isolated_runtime_probe.py) | 共享Linux隔离runner与路径/版本/固定native-load对照，隔离不成立拒绝；无隐式execute。 |
| [qq_application_observation.py](../tools/qq_application_observation.py) | pristine默认应用入口的固定观察，private X环境，不是login/readiness。 |
| [qq_private_window_roles.py](../tools/qq_private_window_roles.py) | private X窗口role metadata，不读标题/图像/输入。 |
| [qq_vendor_stall_observation.py](../tools/qq_vendor_stall_observation.py) | 外部有界stall事实、退出/resource/wait元数据，不注入QQ JS或读内存标题。 |
| [qq_private_inspector.js](../tools/qq_private_inspector.js) | 固定两种runtime/binding metadata inspector表达式，私有loopback、有限预算，不能任意表达式执行或export native值。 |
| [qq_native_host_control.py](../tools/qq_native_host_control.py)、[qq_wrapper_host_entry.js](../tools/qq_wrapper_host_entry.js) | pristine QQ host固定load-only binding实验；复用共享jail，native方法未授权不执行。 |
| [qq_native_init_control.py](../tools/qq_native_init_control.py)、[qq_native_init_entry.js](../tools/qq_native_init_entry.js) | 固定initialize-only实验。 |
| [qq_native_connect_control.py](../tools/qq_native_connect_control.py)、[qq_native_connect_entry.js](../tools/qq_native_connect_entry.js) | 单次offline connect观察，不请求账号/QR。 |
| [qq_native_ready_control.py](../tools/qq_native_ready_control.py)、[qq_native_ready_entry.js](../tools/qq_native_ready_entry.js) | 先等Electron ready再固定offline操作，与native callback readiness区分。 |
| [qq_native_factory_control.py](../tools/qq_native_factory_control.py)、[qq_native_factory_entry.js](../tools/qq_native_factory_entry.js) | pre-initialize factory顺序和handle类型，不把观察当vendor ABI保证。 |
| [qq_prepared_connect_control.py](../tools/qq_prepared_connect_control.py)、[qq_prepared_connect_entry.js](../tools/qq_prepared_connect_entry.js) | prepared-host单次offline connect，复用native_host/controller。 |
| [qq_vendor_bootstrap_control.py](../tools/qq_vendor_bootstrap_control.py)、[qq_vendor_bootstrap_entry.js](../tools/qq_vendor_bootstrap_entry.js) | 原始vendor outer bootstrap固定身份观察，不移植NapCat实现或复制handler。 |
| [qq_ephemeral_host_entry.py](../tools/qq_ephemeral_host_entry.py) | 只在一次性jail中挂载固定wrapper-load main和实验环境，不能当常驻QQ宿主。 |
| [qq_host_dispatch_control.py](../tools/qq_host_dispatch_control.py)、[qq_host_dispatch_entry.js](../tools/qq_host_dispatch_entry.js) | 无应用/native初始化的固定dispatch/timer事实。 |
| [qq_host_dispatch_capture_control.py](../tools/qq_host_dispatch_capture_control.py) | native-free dispatch、有界私有stderr分类，原始输出不公开。 |
| [qq_host_dispatch_roles_control.py](../tools/qq_host_dispatch_roles_control.py) | 外部private-X角色metadata对照，不是窗口截图/交互。 |
| [qq_host_identity_control.py](../tools/qq_host_identity_control.py) | identity-only host对照。 |
| [qq_host_resources_control.py](../tools/qq_host_resources_control.py) | 外部resource对照，不扩原始child限制。 |
| [qq_host_waits_control.py](../tools/qq_host_waits_control.py) | 固定wchan符号/格式观察。 |
| [qq_host_exit_control.py](../tools/qq_host_exit_control.py) | 固定有界public exit metadata。 |
| [qq_host_fatal_control.py](../tools/qq_host_fatal_control.py) | 一次固定fatal观察，raw output仅parent private外部文件。 |
| [qq_host_logging_control.py](../tools/qq_host_logging_control.py) | 单变量logging-enabled对照，有界私有输出。 |
| [qq_host_fsize_control.py](../tools/qq_host_fsize_control.py) | 固定1MiB child FSIZE对照，private capture仍有界。 |
| [qq_fsize_mechanism.py](../tools/qq_fsize_mechanism.py) | 无QQ harmless child resize机制实验，不改实际QQ sandbox。 |
| [qq_prepared_fsize_control.py](../tools/qq_prepared_fsize_control.py) | verified host profile上的prepared-native successor，不是单变量因果保证。 |
| [qq_prepared_phase_control.py](../tools/qq_prepared_phase_control.py) | 固定phase/timer checkpoints，不允许caller选任意native方法。 |
| [qq_bus_admission_control.py](../tools/qq_bus_admission_control.py) | 无QQ private-bus admission对照，共享jail唯一实现。 |
| [qq_bus_capture_control.py](../tools/qq_bus_capture_control.py) | 无QQ bus诊断capture；raw captured bytes不公开。 |
| [qq_bus_identity_control.py](../tools/qq_bus_identity_control.py) | identity-only bus client对照。 |
| [qq_egress_policy.py](../tools/qq_egress_policy.py) | 离线destination plan/地址规则，不是网络隔离或enforcement。 |
| [qq_egress_connector.py](../tools/qq_egress_connector.py) | 显式numeric TCP connect，自己不DNS/listen/activate/isolate。 |
| [qq_egress_rules.py](../tools/qq_egress_rules.py) | 纯selective nft规则编译，不自动apply/resolve/discover/activate。 |

## 11. 外部依赖与许可

- [ASTRBOT_UPSTREAM.json](../vendor/ASTRBOT_UPSTREAM.json#L1-L15)：AstrBot 4.28.1、tag v4.28.1，固定commit `ab42c0d9b726d82ad0f9563e04c53a4460c00d61`、archive SHA256、完整1738文件元数据、upstream_modifications=[]；本轮只读metadata，不改vendor。
- [上游pyproject](../vendor/astrbot/pyproject.toml#L1-L75)：**容器运行Python≥3.12**；外部bootstrap/Lite launcher可为Python≥3.10不是矛盾。依赖包括aiocqhttp、aiohttp/aiosqlite、APScheduler、OpenAI/Anthropic SDK、Pillow、Quart/FastAPI、SQLAlchemy/SQLModel、cryptography、MCP、平台SDK/媒体/文档库等；不表示第一方每条路径都使用所有上游功能。
- [Dockerfile](../deploy/Dockerfile#L1-L42)：Node20构建dashboard，pnpm10.11.0 + frozen-lockfile；Python3.12 slim Bookworm；系统依赖编译库、ffmpeg/codecs、CJK fonts、git/ripgrep；上游Python依赖 `pip install .` **没有Python lock**，源码固定不等于解析出的所有第三方wheel版本完全锁定。
- Windows完整模式依赖WSL2+所选发行版+Docker/Compose v2，Linux完整模式依赖Docker/Compose与Python启动器。Lite仅标准库，DPAPI为Windows系统服务。native实验另需Linuxroot/unshare/mount/chroot/pivot、QQ匹配runtime/native库、私有X/bus等显式条件。
- OneBot/NapCat和腾讯QQ客户端/登录数据外部提供，不随库分发；VM bridge、browser/CDP、邮箱、xdotool GUI执行器也不是fresh compose已部署服务。第三方公网搜索/图片源可变化，源码存在不代表外网可访问。
- [LICENSE](../LICENSE)、[THIRD_PARTY_NOTICES](../THIRD_PARTY_NOTICES.md)：本项目AGPL-3.0-only；保留上游原许可/版权/EULA/声明。复制源码、分发镜像和网络服务的实际许可合规需要按许可处理，不把审计文档当法律结论。

## 12. 已有测试入口、当前验收范围与未完成清单

### 12.1 本文“有测试”不等于本轮“已经通过”

模块盘点本身只读；修复阶段运行了合成提供方/临时磁盘的生产链路集成测试，没有真实上游模型、QQ账号或生产操作。全仓 `python -B -m pytest tests -q --tb=short` 实际结果为 **4991 passed、45 skipped、442 subtests passed**；随后新增损坏元数据、转义摘要和大预算边界测试，收尾定向 **57 passed**。`python -m pyflakes plugin bot_core qq_transport tools tests` 无错误，`tools/validate_release.py` 扫描148文件、failures为空，`git diff --check`通过。最后还修正兼容导出和探针包名告警；不能把测试源文件数当覆盖率。最终统一数字以交付消息为准。

已有核心测试锚点：

- 引擎/复用：[test_bot_harness.py](../tests/test_bot_harness.py)、[test_bot_harness_http_integration.py](../tests/test_bot_harness_http_integration.py)、[test_agent_finish.py](../tests/test_agent_finish.py)、[test_bot_core_contracts.py](../tests/test_bot_core_contracts.py)。
- 事件/队列：[test_message_batch.py](../tests/test_message_batch.py)、[test_local_runtime.py](../tests/test_local_runtime.py)、[test_chat_command_ownership.py](../tests/test_chat_command_ownership.py)、[test_mention_policy.py](../tests/test_mention_policy.py)。
- 任务/防重：[test_tasks.py](../tests/test_tasks.py)、[test_task_process_lock.py](../tests/test_task_process_lock.py)、[test_task_scheduler_fairness.py](../tests/test_task_scheduler_fairness.py)、[test_task_delivery_receipts.py](../tests/test_task_delivery_receipts.py)、[test_task_final_boundaries.py](../tests/test_task_final_boundaries.py)。
- 自主/委派：[test_autonomy.py](../tests/test_autonomy.py)、[test_autonomy_boundaries.py](../tests/test_autonomy_boundaries.py)、[test_autonomous_turn.py](../tests/test_autonomous_turn.py)、[test_delegation.py](../tests/test_delegation.py)、[test_delegated_runner.py](../tests/test_delegated_runner.py)。
- QQ业务：[test_qq_delivery.py](../tests/test_qq_delivery.py)、[test_qq_social.py](../tests/test_qq_social.py)、[test_qq_requests.py](../tests/test_qq_requests.py)、[test_message_transfer.py](../tests/test_message_transfer.py)、[test_group_inspection.py](../tests/test_group_inspection.py)。
- 文件/VM：[test_workspace_tools.py](../tests/test_workspace_tools.py)、[test_vm_jobs.py](../tests/test_vm_jobs.py)、[test_browser_eval.py](../tests/test_browser_eval.py)。
- 独立transport：[test_qq_transport_store.py](../tests/test_qq_transport_store.py)、[test_qq_transport_actions.py](../tests/test_qq_transport_actions.py)、[test_qq_transport_events.py](../tests/test_qq_transport_events.py)、[test_qq_transport_http_api.py](../tests/test_qq_transport_http_api.py)。
- 安装/vendor：[test_embedded_astrbot.py](../tests/test_embedded_astrbot.py)、[test_linux_launcher.py](../tests/test_linux_launcher.py)及其他bootstrap/windows/lite/setup测试。测试本身可能用fixture/mocks；不能替代完整镜像构建、真实提供方、真实QQ账号与外部VM集成。

### 12.2 风险、dead/inactive路径与运维误区

1. **QQ native未完成，严禁写成已独立登录/可替换NapCat**；Action声明coverage、JS dormant controller和offline mock pass都不是native端到端验收。
2. **VM/mail/browser不随fresh compose启动**；缺外部服务时工具会失败，health照样可通过。容器127.0.0.1不能直接当host。
3. **`prepare_container_file`强制NapCat容器名并调用docker cp**，[源码](../plugin/qq_native_actions.py#L98-L115)；fresh Bot镜像并未安装Docker CLI或挂Docker socket。此旧部署适配不能被当成通用文件上传已经可用，且未知上传后staging文件不自动删除。
4. **`up`不是自动升级/迁移**：plugin SHA marker变化直接拒绝；已有cmd_config/panel密码不会被初始化settings覆盖；需要单独受控更新方案。
5. **内存执行锁不是分布式锁**；多Bot进程共享同higan目录可能丢写。任务POSIX flock和SQLitejournal只保护它们自己的store，不保护全部memory/policy/autonomy。
6. **权限是按工具分支实现，不是一个全局owner gate**；第7节差异不能省略。仅“提示词要求只听管理员”不是代码授权检查。
7. **历史/模型window/摘要/事实/观察是不同数据层**；工具观察默认有界且redact，不自动成为长期事实；source中的角色/约定只作为数据，不能上升system。
8. **图片索引有容量不代表原始文件自动删除**；日志/档案/剪贴板/事件outbox缺统一加密与retention机制，需要运营政策，不能假称隐私存储已全覆盖。
9. **legacy仍在源码**：`slang.py`未见现行调用；`_free_tick`是no-op兼容入口；`cmd_list_stickers/cmd_edit_sticker/cmd_del_sticker`保留方法但无command decorator，且on_message阻断slash，不是现行聊天命令入口；旧disabled wake分支/unused direct helpers不应写为启用能力。
10. **fallback不是第二套主引擎**：agent_loop导入失败时 `_decide`只读上下文普通裁决；legacy search marker仍由`_handle_one`接入，不能把所有marker路径一概称dead code。
11. **源码固定≠环境依赖全锁**：Python依赖无lock，Docker base tags也非digest锁；build网络、上游registry和平台差异需单独验收。
12. **停止容器宽限120秒≠永不打断任意长操作**；插件hot-reload会排空活跃工作，但不能因此宣称所有操作系统停止/强杀都零中断。
13. **探针不等于无副作用**：真实model可能收费，VMprobe可写临时状态，native实验可启动受限子进程，配置review即使预览也可能写report；执行须读源码和显式目标授权。
14. **归档与缓存不在现行模块清单**：trash、__pycache__、私有运行库/日志/聊天、QQ账号数据不读取不重新引入；旧私有release_apply/release_access/sid_native_disable/runtime_routing_config已归档，不作为现行安装入口。

### 12.3 审计交付校验与热点锚点

初次模块审计验证252个相对链接存在，并以AST解析152个第一方Python源码（plugin64、bot_core5、qq_transport5、tools76、deploy2）无语法错误；该数字仅标记当时范围。合稿另加入第13节修复与回归链接，最终再次检查相对目标和模块清单；不读取私有状态、不把静态检查当实际模型/QQ验收。

[main.py](../plugin/main.py)热点符号为 `on_message`、`_drain_pending`、`_her_llm_config`、`_dispatch_tool`、`_task_loop`、`_free_tick`、`_vm_tool`、`_decide_tools_first`、`_request_context`、`_send`、`_is_owner`；新增记忆接线随修复移动了行号，按符号定位，不保留失效精确行号冒充最终证据。`_vm_pointer_executor=None`的依赖未就绪边界仍未改变。

### 12.4 本轮修复的验收范围

- 原有条数窗口已从实际前台、主动、本地和任务的历史装配中移除；工具检索分页仍可显式按条数，这是读取 API 不是记忆淘汰政策。
- 摘要验收使用真实 AST 加载的生产方法、真实 JSON 文件和实际 native HTTP 请求构造，仅替换外部模型/QQ边界。验证了模型摘要被保存、进入后续普通请求、重建实例后读取且不重复压缩。
- 唤醒人验收使用真实 on_message 接入，验证 0% 回复/不回复时仍保存、同消息号重传不重写、重建实例读取、腐坏拒写、原子替换失败和并发不丢写。
- 尚未进行：真实上游模型摘要质量评估、真实 QQ 登录与收发、生产重启验证、外部 VM/邮箱服务验收。离线通过不替代这些结果。
- 最终统一测试数量和发布状态记录在交付消息；本文不把未经执行的生产、收费请求或账号操作写成通过。

## 13. 本轮记忆修复：原因、实际链路与配置

后续完整性复核和60%原文/40%摘要归档的饱和数字见[完整性与保留检查](RETENTION_AND_COMPLETENESS_20261030.md)；本文不把窗口退出、摘要有损与真实写盘失败混为“永久删除”。

### 13.1 三个问题的真实原因

| 用户观察 | 源码原因 | 本轮结果 |
|---|---|---|
| 不应按条数保留记忆 | 群池默认240条/30条缓存对齐、当前scope120条窗口、内存2000条、首次恢复200条各自独立限制；消息长短不同但成本相同。 | 实际聊天链路全部改用显式字符预算，恢复/汇总先读完整合格归档；默认缓存也按长度，不再因条数清理。 |
| 摘要没有生效 | `Memory.compress_messages`只构造messages，没有任何主流程调用去执行模型、检查写盘或注入下一轮。 | 新增 `context_summary.prepare` 并接入 `_decide_tools_first`，执行无工具摘要请求、校验、checked写盘和请求注入；旧函数仅兼容无副作用API。 |
| 唤醒人记录没有写盘 | 旧 `WakePersonFacts.add`有save，但消息入口从未调用；自然聊天只进通用history。旧save直接截断JSON且load吞损坏。 | 入口在回复概率之前摄取配置中真实唤醒人的顶层发言；统一原子存储、幂等、来源、损坏拒写，取消每人200条淘汰。 |

“窗口未携带全文”不等于“磁盘删除了记忆”。当前原始归档、派生摘要、长期事实、唤醒人观察和工具观察是五种数据，职责和证据不同，不能互相冒充。

### 13.2 按上下文长度选历史

1. 群聊按真实时间合并所有公共群归档，按 `(scope, native message ID)` 排除当前批次；私聊只读取自己的 exact `private:QQ`，本地入口附加自己的 local 记录，绝不把外来私聊并入公共池。
2. 读取配置预算、生成不含历史的基线上下文，计入人格/system、当前输入、可见事实/观察/任务等基线及当前暴露的工具schema。扣除固定内容与2048字符结构余量后，可用聊天上下文按60%最新完整原文、40%摘要/归档处理分区；不是按全量永久历史的条数或60%比例删除。
3. `size` 采用 compact JSON + `ensure_ascii=False` 的Unicode字符数：文本、字段名、引号、转义、来源元数据都计入；不是只统计正文，也不是按行数或消息数。
4. 从新到旧加入完整记录，遇到不能容纳的记录停止；不会偷偷截断它并冒称全文。被排除的旧记录交摘要链路，原始内容仍可检索。单条过长也不会绕过预算。
5. 最终重建请求再次检查 system/prompt/schema 字符长度，必要时再缩历史；若当前输入/事实/工具本身已经无法容纳，不暗删当前输入或长期事实，记录 `context_budget_exceeded` 并停止该次模型请求。
6. `history_context.coverage`可见 `unit/max_chars/used_chars/returned/omitted/more_available/returned_scopes/max_context_chars/fixed_chars/archive_preserved`，数字是长度诊断或计数报告，不是新的条数保留政策。

显式工具分页 `limit`、旧的 `unified_snapshot(limit=...)`、`history_snapshot(minimum=...)` 仍兼容调用方与历史探针，但不再驱动正常聊天记忆。历史缓存的默认 `append_history` 不再用2000条；已接入的前台、本地、主动、任务/委派和表情审核上下文均不再固定按120/200/6条选择。

### 13.3 有效摘要的完整执行链

```text
原始scope JSONL（不删除）
  → 全部合格历史 → 长度预算 → 最新完整历史后缀
                         └→ 被省略的旧证据，按真实scope分组
                                → 读取同scope旧摘要 + 精确游标
                                → 同一配置模型，无tools/无QQ发送
                                → 非空/不是SILENT/无toolcalls/长度合规校验
                                → reload Memory → checked原子写盘
                                → memory.context_summary 注入普通请求
                                → 重建实例后再次从磁盘加载
```

- `context_compaction.covered_rows`与 `covered_sha256`对应已覆盖的精确源前缀，**不是用条数触发摘要**；只有长度排除才触发。前缀哈希不匹配时从有效来源重建派生摘要，不猜源消息身份。
- 多scope摘要分别保存，不把公共群与私聊揉成全局无来源文本。预算在当前省略scope间分配，摘要条目保留 `scope/updated_at/lossy/source/trust`；输入始终是 `untrusted_data`，输出也不是系统指令/事实/权限。
- 大积压以有限长度chunk逐步处理，本次调用处理到完整可覆盖前缀或失败。单个原始record超过子请求长度时，派生JSON片段显式携带 `source_sha256/offset/total_chars/not_an_independent_message`，不会伪造新消息；`partial_chars/partial_sha256`允许失败后精确续写。
- 只有模型结果合格且 `save_checked`成功才推进游标；空输出、超长、工具调用、SILENT、网络失败或磁盘失败均不伪称成功。原档案不删除，旧磁盘摘要保留；失败状态可见 `summary_failed/memory_unavailable/summary_input_budget_unavailable`，下轮可重试。
- `Memory.set_summary(checked=True)`保存失败会恢复对象旧数据，防止失败的新摘要被随后上下文误用。坏JSON/坏scope结构拒绝覆盖。
- 摘要是有损模型生成内容，需要原始档案作权威依据；离线测试证明接线、保存和使用，不证明任意模型都能准确总结。
- 摘要调用使用原模型配置、无工具，可能额外计费和增加本轮延迟；很大的历史积压可能产生多次摘要请求，不承诺零成本或固定延迟。独立 `reasoning_content` 不当摘要正文。

### 13.4 配置、长度单位与明确限制

在安装器 `info`显示的**外部实例 settings.json**中，保留其它字段并可增加：

```json
{
  "context_memory": {
    "max_context_chars": 600000
  }
}
```

这只是可合并的配置片段，不是用来替换整个settings文件。完整配置仍需 `schema_version:1`及已有模型/实例设置；由 `HIGAN_SETTINGS_FILE`确定路径，禁止在仓库里扫描私人配置。默认600000继承原有摘要阈值作为明确可调字符预算，不根据模型名字臆测；值必须是整数且≥4096，太低容纳不下工具与当前输入时会明确失败，不删除事实凑预算。

**字符不等于token**：各供应商tokenizer、多模态计费/分辨率和模型最大token容量不同。当前没有联网自动获取供应商容量，也没有伪称600000字符必然适配你的模型；应按真实模型能力设置保守值。此轮解决历史保留和摘要接线，不宣称对任意模型做精确token计数。

**本轮未覆盖的长度边界**：首次历史装配后，当轮native transcript仍按原逻辑保留工具调用/结果配对及冻结图片；后续多工具循环、base64图片、工具schema动态曝光可能继续增长。没有破坏配对去强行截断，也没有宣称整个任意长工具运行已实现全局token压缩。永久事实默认全部可见且无条数淘汰，极大的事实/当前输入可能使固定部分单独超过预算；此时不会默默删掉用户长期事实。

**性能与并发边界**：为不再被旧条数窗口漏记，当前每轮读取并合并全部合格JSONL，在历史很大时是O(归档大小)读取/内存成本；没有新增索引数据库或向量库。摘要保存依赖完整Bot的单实例执行锁+checked原子replace，非跨进程事务；不要多进程共享同一记忆目录。源码中的元数据count/游标/工具分页不等于按条数淘汰。

### 13.5 唤醒人消息自动落盘

文件：`HIGAN_ROOT/memory/wake_persons.json`，新增记录格式：

```json
{
  "version": 2,
  "persons": {
    "QQ": [{
      "id": "w<uuidhex>",
      "text": "顶层原始发言或明确模型记录",
      "at": 0,
      "kind": "observed_message",
      "source": {
        "scope": "实际会话",
        "message_ids": ["真实原生消息ID"],
        "messages": [{"id": "真实原生消息ID", "sender": {"id": "QQ", "display_name": "原昵称"}, "body": "原正文"}]
      }
    }]
  }
}
```

示例中的QQ、正文、时间和ID只是字段说明，不是真实账号/原始时间；真实 `at`用采集时epoch，平台提供时另保留 `native_time/time_source`。原有v1行原样保留，首次新增升version2，不猜旧记录的scope、不迁移他人账号、不从昵称合并身份。

- **谁写入**：`on_message → ingest_observed_message → native_record → observe/add → save`。只摄取配置中实际QQ与对应scope的真实顶层Plain和原生消息号；在来源notice过滤后、回复概率/是否发言前执行。0%回复仍可记，不需要模型主动调用工具。
- **什么不伪造**：引用、转发展开、摘要、模型复述、无原生ID、非配置人、错群都不当成本人的自然发言。纯图消息不发明文字，完整非文字证据仍在通用history。
- **观察与归纳分开**：`observed_message`保真实原文；`wp_fact_add`新写是`model_fact`，带真实触发消息来源，不相信模型args传入的任意source。观察不等于已验证事实，更不授予QQ/文件/VM权限。
- **幂等**：同QQ+scope+原生message ID的重传不会再写一条；不同ID相同文字不是重复，不会自动合并。
- **原子与恢复**：同目录临时文件、flush、fsync、`os.replace`；损坏/不可读/schema不合法时fail closed且不覆盖空库，替换失败保原文件/内存并清临时文件。进程内RLock串行短实例reload→modify→save，不宣称跨进程锁。
- **读取与权限**：工具只当前scope可见，未知scope旧行仍在磁盘但不作为当前来源显示；他群/私聊不会借QQ相同暴露；自动记录不改变owner。move先确认目标保存再checked删源，不能无损保留图片/结构时拒绝迁移，避免“成功”丢数据。
- **不自动补造历史**：本轮不扫描私有聊天库回填过去漏掉的唤醒人记录，只有修复后到达的合格消息自动新增；历史回填需另行确认来源、范围与幂等策略。

### 13.6 相关回归与如何判断生效

- [长度窗口测试](../tests/test_history_window.py)、[共享池测试](../tests/test_unified_group_memory.py)、[真实群请求测试](../tests/test_unified_group_flow.py)：>500短消息可同时保留，少数长消息按实际JSON长度裁选，scope来源、当前批次排除和私聊隔离不变，归档搜索仍找得到旧记录。
- [摘要存储与失败恢复测试](../tests/test_context_summary.py)：积压多chunk、超长record显式分段、部分记录失败重启续写、失效哈希、空/SILENT/工具/超长/网络/落盘失败、坏文件不覆盖。
- [实际native接线测试](../tests/test_context_memory_flow.py)：同一真实生产模型调用边界生成摘要→保存→普通请求携带→重建实例不重复调用摘要；失败状态不伪造新summary。
- [唤醒存储测试](../tests/test_wake_person_persistence.py)、[唤醒入口测试](../tests/test_wake_person_persistence_integration.py)：真实入口未回复仍写盘、scope隔离、idempotency、restart、>200保留、原子失败和线程并发。

判断时分别查看：通用scope JSONL是否存在原始record；外部memory.json对应scope是否有summary和有效context_compaction；模型请求里的memory.context_summary是否包含保存过的摘要；wake_persons.json是否有正确QQ/scope/message ID的observed_message。不要分享原始聊天、完整请求、外部settings或带密钥日志；可用coverage长度/状态和测试结果做脱敏检查。线上未更新时不会自动拥有本轮修复。

---

审计范围刻意排除不相关Q-API计费/钱包/relay运维；仓库级维护文档仅用于确定增量、安全、不可泄密与受控发布约束，不将历史服务器事实移植为Bot当前部署事实。
