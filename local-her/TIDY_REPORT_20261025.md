# 工作区整理记录（2026-10-25）

整理人：DSH 会话（彼岸酱运维）

## 一共释放多少

```
整理前  8.5 GB
整理后  5.2 GB
释放    3.3 GB
```

## 删了什么（都可再生或已无用途）

| 类别 | 内容 | 大小 |
|---|---|---|
| pytest 临时目录 | `.pytest-tmp-*`、`.pt-*`、`.testtmp`、`.pytest_cache` | ≈ 2.1 GB |
| 部署中间物 | `_deploy_artifacts_*`、`_diag_ab`、`_dsh_sess*`、`_probe_*`、`_lb*` | ≈ 4 MB |
| 会话历史副本 | `local-her/session-restore/session.v4.jsonl`（原 zip 仍在 Downloads） | 386 MB |
| 安装包 | `local-her/linuxqq.deb`（155 与 186 都有） | 177 MB |
| 安装包 | `local-her/lagrange.tar.gz`（签名服务已死，弃用） | 36 MB |
| 重复压缩包 | `napcat/shell.zip`、`NapCat.Shell.zip`、`OneKey.zip`、`framework.zip` | 38 MB |
| 重复可执行 | `napcat/QQ.exe`（QQ933 里已有） | 300 MB |
| 部署中间目录 | `local-her/deploy155`、`her155.tgz`、`her-bundle.tgz` | < 1 MB |

## 移动/归档了什么（没删）

```
local-her/run*.log / run*.err  →  local-her/_logs/（28 个）
local-her/*.png（截图）        →  local-her/_shots/（归档 27 张，最新 6 张留在原处）
C:\QR                          →  只留 qr186.png / qr186-big.png
```

## 保留且不要动的（她在跑的东西）

```
local-her/venv          578 MB   AstrBot 运行环境（本机副本）
local-her/napcat/QQ933  1,190 MB 便携 QQ（沙盒/本机测试用）
local-her/napcat/shell  111 MB   NapCat Shell（本机测试用）
local-her/data          7 MB     AstrBot 数据（人设库等）
local-her/higan         3 MB     她的记忆/历史/图片（本机副本）
local-her/session-restore 22 MB  会话历史里挖出来的：found/recovered/replay
tools/                 191 MB    运维脚本（含 README_HER_OPS.md 索引）
trash/                 1.44 GB   项目既有归档，不动
guardrail/             394 MB    项目既有目录，不动
```

## 新增的两个说明文件

```
tools/ops/README_HER_OPS.md     她这台机器的脚本索引（按用途分组 + 数据位置 + 禁令）
local-her/README.md             本机 her 目录结构说明
```
