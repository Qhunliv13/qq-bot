# 运维脚本索引

本目录保留针对特定环境编写的巡检、恢复、配置和补丁脚本。文件名中的节点编号、路径及旧参数不能证明当前部署状态；脚本保留不表示适用于当前源码或可以直接重放。

当前插件与边界见 [项目说明](../../README.md)，源码见 [`plugin/`](../../plugin/)。通用探针、策略工具及发布入口位于 [`tools/`](../)，本目录不是一套新的部署实现。

## 按用途查找

| 用途 | 代表脚本 | 执行前注意 |
|---|---|---|
| 连接与巡检 | `run_remote.py`、`check_186.sh`、`probe_napcat_186.sh`、`probe_docker_186.sh`、`reply_stats_186.sh`、`why_silent_186.sh` | 逐项读源码确认查询范围；`run_remote.py` 也支持执行命令和传文件，不是只读工具，密码由 `QAPI_SSH_PASSWORD` 提供 |
| QQ 登录与二维码 | `qr_force_refresh_186.sh`、`qr_wait_fresh_186.sh`、`pull_qr_186.sh`、`get_qr_186.sh`、`post_login_186.sh` | 部分脚本会重启容器、刷新登录或写入二维码文件；可能中断会话并要求重新登录 |
| 人格与提示词 | `export_prompt_bundle_186.py`、`trim_persona_186.py`、`restore_full_persona.py`、`rebuild_persona_exact.py`、`dedupe_tools_dump_186.py`、`facts_only_v2_186.py` | 导出内容可能敏感；写库和文本替换依赖原始格式，不得不经比对覆盖当前人格 |
| 记忆与历史 | `raise_memory_limits_186.py`、`add_remember_tool_186.py`、`redo_facts_186.py`、`fix_memory_bugs_186.py`、`selftest_memory_186.py`、`recover_group_memory.py`、`history_recall_tool_186.py` | 包含测试写入、恢复及旧结构补丁；文件名和旧注释中的容量、遗忘规则不是当前契约 |
| 唤醒与群策略 | `wake_v2b_186.py`、`wake_marker_in_who_186.py`、`at_and_wake_always_186.py`、`at_always_reply_186.py` | 属于特定源码布局下的补丁；当前权限、唤醒和回复行为以插件实现与运行配置为准 |
| 工具与回复适配 | `tool_message_format_186.py`、`optimize_tool_path_186.py`、`fix_dupe_tools_dump_prompts_186.py`、`dedupe_payload_186.py`、`fix_at_target_186.py`、`fix_sticker_pick_186.py`、`fix_see_attachments_186.py` | 不按文件名推断补丁仍缺失，也不串行执行旧补丁重建当前版本 |
| 虚拟机与桌面 | `add_vm_fetch_186.py`、`patch_real_vm_api_186.py`、`prep_desktop_186.sh`、`start_desktop_186.sh`、`inspect_shot_send_186.sh`、`add_self_status_tool_186.py` | 可能写远端文件、启动桌面或触发发送；先确认目标、权限与副作用 |
| 节点安装资料 | `install_155_qq.sh`、`deploy_155_astrbot.sh`、`install_astrbot_155.sh`、`prep_155.sh`、`check_155_running.sh`、`check_155_traffic.sh` | 仅供查阅相应环境的实现，不代表该节点仍在线或业务状态 |

表格是用途索引，不是完整文件清单，也不是验证通过清单；同目录其他脚本同样需先读后用。

## 操作边界

- 修改前确认目标主机、服务、代码版本、数据位置与回滚点；不要把旧路径、节点编号或本机副本当成当前权威。
- 先在当前源码上验证需求，复用已有实现；旧补丁可能重复修改、覆盖新逻辑或依赖已不存在的文本。不能假定所有脚本幂等、自动备份或已实测。
- 未明确需要时不要重启 NapCat / QQ；登录状态可能失效。不要将重启机器人、远程写文件或发送消息当作只读验证。
- 凭据由私有配置或相应环境变量提供；不要把密码、令牌、二维码、数据库、会话及导出提示词提交到仓库。
- 发布工具的存在不构成生产操作授权；先读工具及目标环境约束，核对差异、备份与验证方案，再执行受控变更。

## 档案与协议

[`reports/`](../../reports/) 与根目录发布、部署、审计材料是历史档案，不是本目录脚本当前适用性或远端现状的保证。保留原始报告和脚本，不因整理说明或协议调整追溯改写记录。

本项目采用 GNU Affero General Public License v3.0（AGPL-3.0-only），完整条款见 [LICENSE](../../LICENSE)；第三方依赖遵循各自协议。
