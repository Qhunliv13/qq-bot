# local-her 目录说明（本机副本，2026-10-25）

她是 **<HOST_186>** 上跑的那只 bot（彼岸酱 / Qhunliv-bot）。
本目录是**本机副本 + 测试环境**，生产在 186。

## 目录用途

| 目录 | 用途 | 能不能删 |
|---|---|---|
| `venv/` | 本机 AstrBot 运行环境 | ✗ 删了要重装 |
| `data/` | AstrBot 数据（`data_v4.db` 人设、`plugins/astrbot_plugin_higan`、`cmd_config.json`） | ✗ |
| `higan/` | 她的记忆/历史/图片（`memory/` `history/` `images/` `files/`） | ✗ |
| `napcat/` | 便携 QQ（`QQ933/`）、NapCat Shell（`shell/`）、旧包 | 测试用，可删 |
| `qqbot-profile/` | Windows 第二实例的 QQ 数据目录 | 测试用，可删 |
| `session-restore/` | 从会话历史挖出来的内容（`found/` `recovered/` `replay/`） | 建议留档 |
| `_logs/` | 归档的旧运行日志（run*.log / run*.err） | 可删 |
| `_shots/` | 归档的旧截图 | 可删 |
| `her-bundle/` | 导出的 `persona.txt` / `decision_system.txt` / `tools.json` | 建议留档 |

## 常用命令（本机）

```powershell
# 起本机 AstrBot（若要在本机调试）
$env:HIGAN_ROOT="C:\qhunliv13_web\local-her\higan"
.\venv\Scripts\astrbot.exe run

# 连 186 做事
$env:QAPI_SSH_PASSWORD="<密码>"
python tools\ops\run_remote.py <HOST_186> 22 --cmd "systemctl is-active astrbot-186"
```

## 铁律

```
1. 只重启 astrbot-186，**不要重启 napcat 容器**（会掉登录态，必须扫码）
2. 改 main.py / memory.py / prompts_v2.py 前先备份，改完 py_compile
3. 她的人格/记忆/历史都在 186 的 /root/higan 与库里，本机只是副本
```

整理记录见 `TIDY_REPORT_20261025.md`，脚本索引见 `../tools/ops/README_HER_OPS.md`。
