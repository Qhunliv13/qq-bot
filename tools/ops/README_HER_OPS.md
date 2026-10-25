# 她（彼岸酱 / Qhunliv-bot）运维脚本索引

> 2026-10-25 整理。所有脚本都在 `tools/ops/`，本地主机 **<HOST_186>** 是她的运行机。
> 约定：改动前先备份、改完 `py_compile`、只重启 `astrbot-186`（**不要重启 napcat 容器，会掉登**）。

## 一、连接与巡检

| 脚本 | 用途 |
|---|---|
| `run_remote.py` | 统一远程执行入口（`--cmd` / `--put` / `--get`，密码走 `QAPI_SSH_PASSWORD`） |
| `check_186.sh` | 她的整机体检：服务、端口、数据目录、人设字数、插件版本 |
| `probe_napcat_186.sh` / `probe_docker_186.sh` | NapCat 容器、挂载、端口排查 |
| `reply_stats_186.sh` | 近 N 分钟"回了/没回/命中唤醒/@ 了她"计数 |
| `why_silent_186.sh` | 她为什么不说话（策略配置 + 决策日志） |

## 二、登录与二维码

| 脚本 | 用途 |
|---|---|
| `qr_force_refresh_186.sh` | 强制刷新登录二维码（重启容器 → 等新码 → base64 输出） |
| `qr_wait_fresh_186.sh` | 只等容器自己刷新，不重启 |
| `pull_qr_186.sh` / `get_qr_186.sh` | 取当前二维码到 `/root/qr186.png` |
| `post_login_186.sh` | 登录后核验（上线、OneBot 连接） |

> ⚠️ `docker restart napcat` 会让登录态失效（QuickLogin 失效 ⇒ 必须扫码）。
> 该号近期多次跨环境登录，已触发风控 `serverErrorCode=168`，需在手机 QQ 按提示恢复。

## 三、人格 / 提示词

| 脚本 | 用途 |
|---|---|
| `export_prompt_bundle_186.py` | 导出人格全文、任务规则全文、工具 schema 到 `/root/export/` |
| `trim_persona_186.py` | 精简人格（删掉用户点名不要的段落），并把 Q-API 资料搬进事实 |
| `restore_full_persona.py` / `rebuild_persona_exact.py` | 从仓库/历史复原人设写入 AstrBot 库 |
| `dedupe_tools_dump_186.py` | 工具 schema 去重 + 打印提示词 |
| `facts_only_v2_186.py` | 提示词尾部只保留事实（删掉状态块/摘要/世界书） |

## 四、记忆系统

| 脚本 | 用途 |
|---|---|
| `raise_memory_limits_186.py` | 事实上限（现 2000）、摘要上限、压缩阈值 |
| `add_remember_tool_186.py` | 加 `remember` 工具（她自己决定记什么） |
| `redo_facts_186.py` | 事实带 id、增删改查工具、小数回复率 |
| `fix_memory_bugs_186.py` | 修 `fid` 未定义、模块加载失败 |
| `selftest_memory_186.py` | 记忆模块自检（add/list/edit/delete） |
| `merge_qapi_facts_and_diag_186.py` | 把散条合并成一条（Q-API 资料） |
| `forget_instead_compress_186.py` | 不再压缩：超 400 条直接忘掉最早 60% |
| `recover_group_memory.py` | 从群历史重建记忆（翻到最早一条再正序） |
| `history_recall_tool_186.py` | 加 `history_search` / `history_recent`（翻已遗忘的档案） |

## 五、唤醒人 / 唤醒词

| 脚本 | 用途 |
|---|---|
| `wake_v2b_186.py` | 唤醒表改为按群存储 + 8 个工具（list/search/add/remove/wp_fact_*） |
| `wake_marker_in_who_186.py` | 唤醒人消息带结构化标记（按 QQ 号，含"我记了他 N 条"） |
| `at_and_wake_always_186.py` | @ 她 / 唤醒人说话 ⇒ 必进回复判定（模型仍可沉默） |
| `at_always_reply_186.py` | 上面那条的前置补丁（@ 判定） |
| `add_vm_fetch_186.py` | `vm_fetch`：把群里文件下载进她的虚拟机 |
| `patch_real_vm_api_186.py` | 真正在跑的虚拟机 API（`/root/higan/vm_api.py`）加 `/vm/download` |

## 六、回复质量 / 工具链路

| 脚本 | 用途 |
|---|---|
| `tool_message_format_186.py` | 工具结果以标准 `tool` 消息回喂（DSH/OpenAI 同款） |
| `optimize_tool_path_186.py` | 撤硬去重 + tool 消息后补原始用户消息 |
| `fix_dupe_tools_dump_prompts_186.py` / `dedupe_payload_186.py` | 修上游 400 `Tool names must be unique` |
| `fix_at_target_186.py` | 修"别人 @ 主人她却当成叫自己" |
| `fix_sticker_pick_186.py` | 表情包不再永远发第一张：命中才发、多张给候选 |
| `fix_see_attachments_186.py` | 文件/语音/视频渲染成带名字大小的标记 |
| `fix_persona_and_dup_186.py` / `dedupe_reply_186.py` | 人格加"可以沉默"、发送去重（后者已撤） |

## 七、虚拟机与桌面

| 脚本 | 用途 |
|---|---|
| `prep_desktop_186.sh` / `start_desktop_186.sh` | 给她的桌面加任务栏、图标、壁纸（截图不再只有浏览器） |
| `inspect_shot_send_186.sh` | 截图与发送路径排查 |
| `add_self_status_tool_186.py` | `self_status`：一次看全部设置与记忆状态 |

## 八、155 节点（已停用，仅留档）

`install_155_qq.sh` / `deploy_155_astrbot.sh` / `install_astrbot_155.sh` / `prep_155.sh` /
`check_155_running.sh` / `check_155_traffic.sh` —— 155 已按用户指示停用业务，这些仅作记录。

## 数据位置（186）

```
/root/astrbot-src/data/data_v4.db            人设（personas.higan）
/root/astrbot-src/data/plugins/astrbot_plugin_higan/   插件（main.py / memory.py / prompts_v2.py）
/root/higan/memory/memory.json               长期事实
/root/higan/memory/wake_persons.json         唤醒人专属记录
/root/higan/groups.json                      群策略（回复率/只看@/唤醒表）
/root/higan/history/*.jsonl                  全量聊天档案
/root/higan/images/                          截图与图片（sticker 在 data/stickers）
```
