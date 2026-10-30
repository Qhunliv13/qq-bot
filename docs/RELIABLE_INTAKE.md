# 待处理消息可靠性与长度拆分

> 2026-10-30，源码离线验证，不代表生产上线。60%原文/40%摘要分区不变；本次优化补上待处理机会、输入长度与写盘回执，绝不把未知外部操作自动重试。

## 状态与恢复

唯一实现为[pending_inbox](../plugin/pending_inbox.py)，装配为[inbox_runtime](../plugin/inbox_runtime.py)和[main](../plugin/main.py)。完整Bot在外部HIGAN_ROOT下创建pending_inbox.sqlite3；不修改QQ账号、旧history、事实和宿主配置。

```text
真实原生输入 -> pending -> 原子claim -> running -> completed / failed / blocked
进程启动后遗留 running -> uncertain（隔离，不自动回 pending）
未开始的 pending -> 下一条真实同平台/账号事件验证 -> 当前策略复核 -> 有序恢复
单条超长或策略拒绝 pending -> blocked（不执行，不删原文）
```

- 原生QQ来源必须有aiocqhttp适配器类型、精确origin/scope、真实sender/self、可验证native message ID；去重键为scope/origin/self/native ID，所有终态仍保留防重复投递。不按文本相同去重，不自动清理/过期。
- SQLite采用BEGIN IMMEDIATE与EXTRA同步，线程与独立进程并发认领/幂等有合成测试。文件首次独占创建，POSIX权限0600并同步父目录；文件消失/空文件/损坏/外来schema/校验不符时不建空库顶替。首次创建撞车的一方可能安全拒绝，不能当永远无错。
- 只有数据库确认pending项可恢复。用下一条真实适配器事件确认平台和账号，不联网登录、不凭配置猜QQ，不重构WebChat浏览器response queue或本地root capability。平台/账号不同的pending仍留原库。
- 恢复来源保留sender、native ID、正文、mentions、quote、native_time与forward IDs；角色由当前配置和真实身份重新计算，不从存储文字/昵称/role字段提权。发送复用已有TaskEvent原生序列化和回执，不实现第二套QQ发送器。
- 源载荷不保存完整raw payload、权限、历史、凭据。顶层附件保留安全元数据，签名/查询/userinfo等URL能力明确标不可用，不下载、不改造成已看见的像素。正文/引用自身含这类URL时整项返回unsupported，不截正文伪装完整。单项UTF-8载荷超过2MiB同样unsupported。
- unsupported只表示无法持久恢复，当前活请求仍走原链；队列存储异常则关闭该次执行，不绕过持久claim偷偷发送/操作。日志只给安全原因，不输出原始异常、消息或数据库路径。
- claim失败前没有外部效果；清理本地映射后，下条真实输入可恢复数据库仍确认pending的记录。claim已提交、发送结果未知或任务取消时不自动重复执行；遗留running直到初始化隔离uncertain。completed只表示该轮结束，包括静默/概率拒绝，不证明已发消息。

## 按长度处理慢请求积压

[intake_batch](../plugin/intake_batch.py)在已有全局执行锁内，用实际system、完整工具schema、当前事实/观察/输入序列化长度和结构余量测量，二分选择能容纳的最大完整有序前缀。保留消息边界、身份和原生ID，不按条数截断、不把剩余消息丢掉。

- 等待期间到达的消息仍进入当前或下一批；执行中的模型/工具/发送不被新消息取消。
- 超大积压可分多轮执行；每轮仍使用既有60/40历史/摘要装配，可能多次生成摘要与模型请求，不保证原来的延迟/费用/回答措辞完全相同。
- 单条不可约超长输入明确警告intake_archive_only，并blocked；后续正常输入继续处理。固定人设/工具/事实本身太大同样无法保证可执行，应降低固定成本/改预算，不编造缩短后的原始消息。
- WebChat仍逐个活请求处理，避免把回复发到另一个response queue；本地入口继续其既有单请求预算。图片像素、后续工具transcript增长不受这个首次文字测量完整约束；不是tokenizer或全程上下文治理。

## 历史确认与防重

[history](../plugin/history.py)新增Recorder.append_checked；兼容append复用同一实现并返回回执。

- 成功为persisted=true与duplicate标志；写、flush、fsync、close等失败不报成功，checked抛受控HistoryPersistenceError，兼容入口返回persisted=false/error/uncertain。
- 完整原生记录独立保存，预览仍最多2000字符；精确scope/kind/source/type-preserved ID去重。没ID的同文本仍逐条保留；发送记录与接收记录分区，不把不同类型当重复。
- 损坏UTF-8、非JSON、半行等档案保持原字节且拒绝追加；不擅自修复/截尾。部分写入或fsync失败可能留下未确认字节，故uncertain不等于已回滚。
- 同进程所有Recorder实例使用共享锁，stat缓存避免每次完整扫描；外部改变使缓存失效。它不是跨进程JSONL事务，别让多个完整Bot共享全部状态目录。
- 入站和已发送回复都检查回执并记录未确认落盘；发送历史失败不重发已公开消息。历史读取仍有legacy best-effort路径；不承诺物理故障下零丢失。

## 检查与残余边界

验证入口：[持久箱事务](../tests/test_pending_inbox.py)、[真实恢复接线](../tests/test_inbox_runtime_flow.py)、[写盘故障](../tests/test_history_persistence.py)、[精确长度分区](../tests/test_intake_batch.py)、[80条积压链路](../tests/test_intake_split_flow.py)。全部使用临时目录/合成提供方，无生产QQ或上游模型调用。

```text
python -B -m pytest tests/test_pending_inbox.py tests/test_inbox_runtime_flow.py tests/test_history_persistence.py tests/test_intake_batch.py tests/test_intake_split_flow.py -q
```

核心定向91项通过，包括认领故障后恢复、终态竞争清理、跨账号隔离和无权/超长映射清理。冻结主体代码全仓回归 **5147 passed、45 skipped、460 subtests passed**（351.46秒），pyflakes与发布152文件扫描通过。之后进一步将main的历史工厂改为正常import复用同一个进程锁，并新增[工厂回归](../tests/test_history_factory.py)；含最后工厂小补丁、恢复/拆分/写盘/原有合批与60/40链路的定向 **109 passed**（16.28秒），随后pyflakes、发布152文件扫描、diff检查通过；不冒称该最后小补丁另跑了全仓。

残余：首次落盘前故障、unsupported瞬时输入、未知外部结果不能安全自动重试；无队列终态清理与磁盘配额；永久原始归档和防重记录会增长，摘要/全历史读取仍有耗时；进程重启后需下一条真实账号事件才恢复pending；不把SQLite校验和称为防本地篡改认证；未新增生产监控或自动运维。其他未完成能力见[完整性检查](RETENTION_AND_COMPLETENESS_20261030.md)。
