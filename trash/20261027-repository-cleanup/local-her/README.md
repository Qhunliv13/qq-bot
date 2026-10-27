# local-her：本机调试资料

本目录在源码仓库中只包含说明与整理档案，不包含 AstrBot、NapCat、QQ 登录状态、运行数据库或聊天记忆。插件源码位于 [`plugin/`](../../../plugin/)，测试位于 [`tests/`](../../../tests/)，运维脚本索引见 [README_HER_OPS.md](../tools/ops/README_HER_OPS.md)。

## 本机环境边界

部署者可自行准备以下目录；它们不是仓库已提供的内容，也不是可删除清单：

| 目录 | 本机用途 |
|---|---|
| `venv/` | Python / AstrBot 运行环境 |
| `data/` | AstrBot 配置、插件和数据库 |
| `higan/` | 插件记忆、群策略、历史与附件 |
| `napcat/`、`qqbot-profile/` | NapCat / QQ 运行环境及登录资料 |
| `session-restore/`、`her-bundle/` | 会话恢复资料与导出内容 |
| `_logs/`、`_shots/` | 日志与截图 |

这些目录可能含个人信息、聊天内容或凭据，不应提交。清理、恢复或迁移前先核对实际路径和数据权威，并备份；本机副本不能证明远端现状，也不应直接覆盖远端数据。

## 检查与运行

在仓库根目录执行离线检查：

```bash
python -m pytest tests -q
python -m compileall -q plugin
```

实际运行需单独安装 AstrBot 与 NapCat，配置模型和 OneBot 连接，并检查插件的数据路径。`HIGAN_ROOT` 仅指定使用该配置的模块；仍有模块保留默认绝对路径，不能只设置一个环境变量就认定迁移完成。远程地址、服务名和凭据由部署者提供，不从旧档案推断。

## 档案与协议

本目录保留的整理报告是历史工作区记录，不是当前目录清单、部署契约或操作授权；不得依据其中的“可删”结论直接清理数据。报告原文保留，不随现状说明或协议调整追溯改写。

本项目采用 GNU Affero General Public License v3.0（AGPL-3.0-only），完整条款见 [LICENSE](../../../LICENSE)；第三方依赖遵循各自协议。
