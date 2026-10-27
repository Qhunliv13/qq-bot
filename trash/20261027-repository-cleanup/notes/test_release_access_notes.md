# SSH 管理员受控插件生效

目的：只用已有 root SSH 权限部署，不额外索取 Dashboard 密码，不伪造 JWT、
不重置密码、不重启 AstrBot 或 NapCat。

管理员在运行主机调用 AstrBot 自己的 ApiKeyService 创建仅 `plugin` scope 的
临时运维 key。框架最短有效期是 1 天，不支持小时值；发布结束立即 revoke，
失败也在 finally 中尝试撤销。不删除审计记录，不影响已有 key。

创建前对实际 SQLite 数据库使用 backup API 得到一致备份；不改 schema、不运行迁移。
私密文件权限 0600；key 不出现在命令参数、日志、报告、Git 内容中。

这是受控发布步骤，不属于只读巡检；必须先将脚本提交，核对分发 hash，才能执行。
仅查询/重载明确注册的 `astrbot_plugin_higan`，空名称或未知名称绝不传给 reload。
重载前预检代码/依赖和身份。新版先等待旧实例在途工作完成，清理旧实例闲置循环，
再开放接收与后台任务；超时不重启、不取消未完成的模型/发送任务。
