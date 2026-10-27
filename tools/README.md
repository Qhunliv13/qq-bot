# 工具索引

这些脚本依赖各自指定的环境、源码位置和权限。文件名不证明只读、幂等或适用于任意部署；执行前阅读源码和 `--help`。历史一次性补丁位于 [归档](../trash/20261027-repository-cleanup/tools/ops/)，不作为当前操作入口。

## 离线检查

在仓库根目录执行：

```bash
python -m pytest tests -q
python -m compileall -q plugin
python tools/validate_release.py
```

- [validate_release.py](validate_release.py)：检查插件和工具文件的语法及凭据模式。
- [tool_contract_audit.py](tool_contract_audit.py)：工具契约与真实分发代码的离线审计；外部接口模拟结果不是线上操作成功证明。

## 发布与配置

| 工具 | 用途与边界 |
|---|---|
| [release_apply.py](release_apply.py) | 指定插件的基线／清单核验、备份、文件分发和优雅重载；仅适用于源码指定的部署目标 |
| [release_access.py](release_access.py) | 创建或撤销受限发布权限；需合法管理授权，不是公开服务接口 |
| [set_group_policy.py](set_group_policy.py) | 管理群策略；会写配置 |
| [command_toggle.py](command_toggle.py) | 管理命令状态；会改变运行配置 |
| [sid_native_disable.py](sid_native_disable.py) | 管理指定原生命令配置；执行前核对目标与备份 |
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
| 缓存与历史 | [prompt_structure_cache_probe.py](prompt_structure_cache_probe.py) 默认离线比较同内容布局，显式 `--live` 才发四次隔离模型请求，输出真实缓存 token；合成场景不代表生产整体收益。[cache_layout_probe.py](cache_layout_probe.py)、[cache_prefix_probe.py](cache_prefix_probe.py)、[history_cache_probe.py](history_cache_probe.py) |
| 图片发现 | [image_search_probe.py](image_search_probe.py) |

密钥和运行数据库不随源码分发。输出记录应保存在受保护的独立目录，不提交聊天内容、原生请求 flag、二维码或令牌。
