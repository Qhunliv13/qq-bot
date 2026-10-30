# 功能完整性与聊天满载保留检查（2026-10-30）

> 对象：当前完整 Bot 源码，以 `41984f0` 为修改前基线；不含生产实例巡检。没有读取私有聊天/账号/配置，没有付费请求模型、登录QQ、操作线上实例。本文区分“原文没进入本次模型”“摘要有损”“磁盘归档是否存在”，不把三者合成“丢弃”。

## 一、结论与本轮调整

1. 修改前确实不是60/40：总上限600000时固定预留75000（1/8）给摘要，原文使用余量。现在扣除实际固定输入与2048字符结构余量后，**可用聊天区60%原文、40%摘要/归档**。
2. 比例按序列化Unicode字符长度，不按消息条数。原始JSONL没有因窗口满而滚动删除；摘要失败时退出模型原文窗口的部分仍应从归档检索，不能称为已有效压缩。
3. 不承诺把无限增长的永久历史60%永远放进固定上下文；更多历史累积后，当轮原文占全档案的比例会下降，旧证据由有损摘要/归档承接。
4. 完整功能仍存在未完成与部署依赖，详见下表。60/40不解决供应商token容量、图片/多轮工具增长、无磁盘空间、外部服务未接入等问题。

## 二、预算公式

唯一实现见[预算模块](../plugin/context_budget.py)，接入见[生产消息循环](../plugin/main.py)的 `_decide_tools_first`：

```text
A = max(0, 最大上下文字符 - 实际固定内容字符 - 2048)
最新原始聊天区 = floor(A × 60%)
摘要区 = A - 原始聊天区
```

固定内容包括人格/system、当前输入、可见事实/观察/任务、暴露工具schema。预算使用 `size` 的compact JSON Unicode长度，不假称精确token计数。最新完整记录按时序后缀进入原文区；记录边界可能使原文区未占满，单条比原文区还大则整条从原文窗口退出，供分段摘要/归档。不会编造完整消息，也不按条数填充。

摘要区不强行填到40%：没超出历史就不调用摘要；已有短摘要则留空，不生成无价值内容凑长度。摘要输出可有损，磁盘原始证据仍是权威。摘要长度、可见分区以及落盘覆盖情况均可在结构化上下文元数据检查。

## 三、满载到底退出多少

可复现离线探针：[context_retention_probe.py](../tools/context_retention_probe.py)。运行 `python -B tools/context_retention_probe.py`，只生成合成消息，不读取任何运行数据，不联网。以下为总上限600000、固定内容50000的结果：

- 可用聊天长度547952；原文上限328771；摘要上限219181。
- 旧原文上限472952，新原文上限328771，少144181字符空间改用于摘要区，不是磁盘删除144181字符。

| 合成输入 | 原文保留记录 | 原文记录字符 | 转摘要/归档记录 | 转摘要/归档记录字符 | 原文占此合成全档案 | 自动删除归档 |
|---|---:|---:|---:|---:|---:|---:|
| 100条，每条正文50字符 | 100 | 10190 | 0 | 0 | 100% | 0 |
| 1000条，每条正文500字符 | 593 | 327929 | 407 | 224961 | 59.31% | 0 |
| 100条，每条正文10000字符 | 32 | 321664 | 68 | 683526 | 32.00% | 0 |
| 1条，正文600001字符 | 0 | 0 | 1 | 600052 | 0% | 0 |

记录字符是逐条compact JSON长度之和，包含本探针scope/id/body字段，不含数组括号/记录分隔逗号；真实记录metadata不同会改变数字。这个表不是生产群统计、不是平台token计费、也不是承诺固定保留593条。对应旧策略1000条合成输入原文保留853条，退出147条/81181记录字符；新策略更多旧证据转摘要或归档，为摘要提供更大预算。

### 诊断字段

- `conversation.history_context.coverage.allocation`：可用长度、raw/summary上限、0.6/0.4比例与结构余量。
- 同coverage的 `source_chars/retained_source_chars/omitted_source_chars/retained_source_fraction`：本轮实际历史原文保留/退出长度，不是拍脑袋认为固定40%。
- `memory.context_summary.archived_source_records/archived_source_chars`：退出本次原文区的原始证据。
- `summarized_source_records/summarized_source_chars`：哈希校验通过且已持久摘要覆盖的完整源记录；不把半条摘要当整条覆盖。
- `archive_only_records/archive_only_chars`：尚未被有效持久摘要完整覆盖的退出内容，仍依赖原始档案检索。摘要失败或损坏时保守计入此类。
- `archive_deleted_records=0`：本次窗口策略没有删除档案，**不是声称磁盘永不写失败、旧文件永不损坏或旧显示文本没有截断**。

## 四、慢请求与新消息积压

[入口与队列](../plugin/main.py)在 `on_message` 记录真实消息后加入scope队列；正在模型/工具/发送中的轮次不被新消息取消。`_drain_pending`取得共享执行锁后才detach一批，等待期间到达的消息进入这一批或下一批。WebChat每请求有自己的response queue，因此按请求逐个处理，不把回复投到别人的请求。

**没有因为“慢了”自动按数量丢消息或按消息年龄过期的代码**；但这不等于无限可靠消息系统：

- 后续可靠性优化已为可验证的原生QQ输入加入SQLite持久待处理箱：未开始项在下一条相同平台/账号的真实输入到达时恢复，保持顺序；已开始但结果未知项启动时隔离为uncertain，**不自动重试模型/工具/发消息**。WebChat/local的瞬时回复能力不伪造重建，不承诺崩溃恢复；无有效原生ID/路由、敏感URL正文或超过2MiB载荷会明确报告unsupported并仅保留当前活处理路径。首次落盘前崩溃仍可能丢处理机会。
- 取得执行锁后按真实system/schema/当前消息序列化长度拆出最大有序完整前缀，其余仍排队。不按条数、不截消息。单条本身仍超长时明确archive-only并blocked，继续处理后面的正常消息；不假称该单条已执行或无损摘要。
- 出队时策略复核会排除已不允许回复的消息；这是拒绝回复，不是删除归档。
- 图片每次实际送模型最多3张像素；更多图片的来源记录不等于所有原像素均已看到。大量图片不能只用文字预算推断无损。
- 原始Recorder现提供append_checked与flush/fsync成功回执，兼容append也返回persisted/duplicate/uncertain；入口和发送后会明确记录未确认落盘，不因发送历史写失败重复发送。损坏/半行归档拒绝追加且保留原字节；无原生ID不按正文去重。仍无法给出“任意故障下实际丢失严格为0”的承诺；多进程JSONL写入尚不承诺事务化。没有生产磁盘与真实记录核验授权，本次不扫描账号目录。

回归：[batch_flow](../tests/test_batch_flow.py)、[真实比例与积压测试](../tests/test_context_memory_flow.py)、[长度保留测试](../tests/test_context_retention_policy.py)。

## 五、仍不完整、未接线或依赖外部的功能

| 优先级 | 功能与当前状态 | 精确边界/证据 | 本轮处理 |
|---|---|---|---|
| 高 | 原生独立QQ后端尚未完成 | [native_backend.js](../qq_transport/native_backend.js)的 `getSnapshot`仍 `independent_backend_ready:false`；动作声明/离线机制不等于真实native handler、完整登录/收发 | 如实保留未完成，不登录/替换现有OneBot |
| 高 | 全程上下文长度治理未完成 | 首次历史装配有长度预算；后续native工具transcript、图片data URL、动态schema仍可增长 | 不破坏tool-call配对强截；不宣称精确token或全程预算已完成 |
| 高，已改善 | 原生持久队列与批次长度拆分 | [pending_inbox.py](../plugin/pending_inbox.py)/[inbox_runtime.py](../plugin/inbox_runtime.py)持久pending、claim-before-work与uncertain隔离；[intake_batch.py](../plugin/intake_batch.py)有序完整前缀拆分 | 可恢复未开始的有效原生输入；瞬时WebChat、unsupported与单条超大仍有边界，不承诺exactly-once外部效果 |
| 高，已改善 | 通用历史归档写失败回执 | [history.py](../plugin/history.py)提供checked/fsync/幂等原生ID、兼容失败receipt，入口明确日志；旧line仍最多2000字符，typed record独立保全文 | 不吞成成功；损坏文件原样保留并拒写；多进程JSONL事务/磁盘耗尽自动恢复仍未解决 |
| 高 | 工具授权不是统一管理员门禁 | [main.py](../plugin/main.py)普通group_policy、任务按ID管理、VM/workspace与owner-only申请是不同分支 | 审计报告，不擅自更改用户权限政策 |
| 中 | VM/浏览器/邮箱依赖外部桥 | [_vm_tool](../plugin/main.py)请求独立VM HTTP bridge；[compose](../deploy/compose.yaml)不部署该服务 | 工具目录存在不等于外部服务已就绪 |
| 中 | 可视拖拽/轨迹执行后端默认未安装 | [main.py](../plugin/main.py) `_vm_pointer_executor=None`；[vm_pointer_runtime.py](../plugin/vm_pointer_runtime.py)依赖显式executor | 保留安全拒绝，不用错误fallback绕过验证 |
| 中 | 文件上传适配不通用 | [qq_native_actions.py](../plugin/qq_native_actions.py) `prepare_container_file`依赖固定NapCat容器与docker cp；fresh镜像无Docker socket/CLI | 标部署依赖，不宣称fresh可通用上传 |
| 中 | 安装up不是升级迁移 | [entrypoint.py](../deploy/entrypoint.py)插件内容SHA marker会拒绝不同旧volume静默升级 | 不修改线上数据/旧标记以绕过保护 |
| 中 | 主动加群/好友/回答验证不可用，申请回补/业务核验未完成 | [qq_requests.py](../plugin/qq_requests.py)显式 `native_capability_unavailable`；`_capabilities`还标 runtime_verified=false、friend_native_backlog/group_native_sync/membership_verification=false；原生事件接收审批是独立已有路径 | 不用未注册API伪装成功，不把已发审批当实际进群/加好友 |
| 中 | 读取全归档与摘要积压仍可能慢 | 全量JSONL读取合并；多chunk摘要在当前轮执行，涉及额外模型请求 | 60/40不承诺低延迟；失败保档案而非丢弃 |
| 低 | 黑话学习模块未接主链路 | [slang.py](../plugin/slang.py)有独立存储接口，但主流程没有自动调用 | 不列为已启用自动功能 |

完整逐模块架构仍见[全系统文档](SYSTEM_ARCHITECTURE_STATUS_20261030.md)。

## 六、真正会截断/淘汰的其他数据层

用户聊天原始typed record、长期facts、wake_person观察与以下数据不是同一种保留政策：

- [tool_context.py](../plugin/tool_context.py)：工具观察每scope最多100条、结果4000字符、单记录12000字符、进prompt16000字符；旧工具观察会compact，不能称“所有工具输出全文永久保存”。
- [delegation.py](../plugin/delegation.py)：结果保存受10000字符上限，标 `result_truncated`；不是无损长报告仓库。
- [main.py](../plugin/main.py)：记住图片引用最多5个、每次模型像素最多3张；表情索引上限300且存在老表情清理逻辑；与聊天记录60/40不同。
- [history.py](../plugin/history.py)：兼容line预览最多2000字符；typed record保留完整元数据。无typed record的旧行无法从截断预览恢复原文。
- 摘要是有损派生文本，投影按JSON长度可能显式标 `projection_truncated`；原始归档不因摘要生成删除。

本轮不把这些既有安全/容量边界统统改成无限保留；需要分别确定用途、磁盘政策和外部依赖后再做后续改造。

## 七、60/40首轮验证与顺带修复（08c9067）

- [保留比例测试](../tests/test_context_retention_policy.py)、[native接线与积压](../tests/test_context_memory_flow.py)、[摘要边界](../tests/test_context_summary.py)、[共享群链路](../tests/test_unified_group_flow.py)收尾定向 **90 passed**；现有合批/主动/本地/任务/主动@链路另 **66 passed**。
- 60/40原始预算测试保留80条当前输入与160条原始档案；直接调用模型时不可约的超长当前批次仍安全拒绝。后续[长度拆分链路](../tests/test_intake_split_flow.py)覆盖真实入口80条×900字符在60k预算下拆多个有序批次，以及单条超大明确归档后继续正常消息；[恢复接线](../tests/test_inbox_runtime_flow.py)覆盖未开始重启恢复、已开始隔离、重复投递、账号隔离、策略复核和落盘失败。
- 全仓过程中真实暴露[轻量网页](../tools/lite_web.py)拒绝401/403/413/415等请求时未读body即关连接，在Windows可能产生连接中断而不是稳定错误响应。本轮以唯一入口有界丢弃拒绝请求body修复；只读唯一明确的ASCII Content-Length、拒绝TE/重复/巨大长度、最多262144字节和0.5秒绝对期限，不解析JSON、不执行模型、不放宽有效JSON131072字节门槛。
- 该HTTP修复的[轻量网页测试](../tests/test_lite_web.py)与原生进程回归 **28 passed、99 subtests passed**，覆盖未提交body不挂起、无权/格式错误不调用模型、巨大/歧义长度不读取、超时恢复。Lite仍是与完整Bot分离的无持久记忆文字入口，60/40策略不冒充已应用到Lite。
- 静态检查pyflakes无错误；发布扫描149文件failures为空；文档目标与Python语法额外校验。最终冻结代码全仓回归为 **5056 passed、45 skipped、460 subtests passed**（321.11秒）；pyflakes、发布149文件扫描与差异检查均通过。发布提交以交付消息为准，不将离线合成测试写成真实QQ/模型/VM或生产通过。
