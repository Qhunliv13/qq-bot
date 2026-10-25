# 彼岸酱（Qhunliv-bot）源码与运维

一只跑在 Linux 上的 QQ 群聊 bot（AstrBot + NapCat）的运维与调试脚本合集。
生产插件源码与运维脚本集中管理。按最新发布要求，只移除密钥、密码、令牌等秘密；
源码中的非秘密身份标识、默认路径和运行参数保留，不再全量占位化。
新增测试和发布工具的验证范围见发布记录，不宣称全部历史脚本都已重新实测。

## 目录

- `plugin/` —— 从现网核对后的完整插件源码（密钥从私有运行配置读取）
- `tests/` —— 离线工具回路、结果持久化、生产适配回归
- `tools/production_probe.py` —— 真实模型与只读工具隔离验证，禁止发 QQ 消息
- `tools/release_apply.py` —— hash 核验、备份、指定服务激活和回滚
- `tools/validate_release.py` —— 发布文件语法和密钥模式检查
- `tools/ops/` —— 历史运维脚本（按用途分组说明见其中的 `README_HER_OPS.md`）
- `local-her/README.md` —— 本机调试目录说明
- `local-her/TIDY_REPORT_20261025.md` —— 一次工作区整理的记录（方法可复用）

## 说明

- 脚本以"能重复执行"为前提写就，关键操作前都会备份、改完都会编译校验。
- 涉及人格/记忆/提示词的改动只做增量补丁，不整段重写。
- 历史运维脚本仍保留原有占位符；新 `plugin/` 源码按要求只脱敏秘密，不抹除非秘密配置。
- 本地测试：`python -m pytest tests -q`；编译：`python -m compileall -q plugin`。
- 不提交运行数据库、聊天档案、工具观察文件、Dashboard 或上游凭据。