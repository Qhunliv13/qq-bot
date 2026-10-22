# README 是 AI 写的

# 彼岸酱 · QQ 群聊人格插件（AstrBot）

一个跑在 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 上的 QQ 群聊人格：
她有自己的虚拟机（Xvfb + Chrome + VNC），能自己看屏幕、上网、点页面、跑脚本、
收发邮件、发图片/视频/文件、发自己收藏的表情包；聊天历史落盘，记忆跨群共享。

> 本仓库是**脱敏后的公开版**：QQ 号已替换为占位符，邮箱/密码/令牌等凭据不在仓库中，
> 运行时从本机配置文件读取。

## 关于 / 体验

- 官网：<https://aiapi.qhunliv13.online>
- 交流群：**146045691**（Q-API 中转站）
- 想让她在群里干活、或者问部署细节，都欢迎进群聊。

## 能力一览

| 模块 | 说明 |
|---|---|
| 决策 | 群聊里自己判断要不要说话（可以沉默）；被点名时优先响应 |
| 跨群视野 | 一份**结构化聊天记录**：按群分组、当前群标「现在」，不硬隔离各群 |
| 工具回路 | `agent_loop.py`：模型自主调工具 → 看结果 → 再决定，最多 3 轮，失败回退纯文本 |
| 虚拟机 | 截图 / 开网页 / 点击（两段式：预览画标记 → 确认真点）/ 打字 / 按键 / 执行脚本 / 读写文件 |
| 浏览器 | 标签页操作（关/开/切换/恢复/reload/只留一个）、按文字点击、iframe 内取值、当前标签内导航 |
| 视觉 | 截图会**喂回模型**，她能"看见"屏幕再说话（取不到的图自动丢弃，不拖垮整轮） |
| 媒体 | 发图片 / 视频 / 语音 / 任意文件；表情包（按描述匹配） |
| 邮件 | 通过 Microsoft Graph（refresh_token）读最近邮件并提取验证码 |
| 记忆 | 群内摘要 + **跨群共享的全局摘要**（合并保留最近若干段）+ 事实库 |
| 历史 | 每条消息落盘 `history/*.jsonl`，可按关键词检索（重启不丢） |
| 任务 | 后台任务（定时 / 脚本触发 / 多步），支持暂停、完成、取消 |

## 目录

```
main.py         插件主体（事件处理、决策、工具分发、发送）
agent_loop.py   工具回路（纯函数，可单测）
prompts_v2.py   系统提示词与消息组装（前缀缓存友好：稳定 system + 追加式 transcript + 尾部易变块）
memory.py       L2 摘要 + 全局摘要 + 事实
history.py      聊天历史落盘与检索
tasks.py        后台任务
slang.py        俚语/黑话学习（可选，默认未启用）
metadata.yaml   插件元信息
```

## 安装

1. 把 `astrbot_plugin_higan/` 整个目录放进 AstrBot 的 `data/plugins/`。
2. 在 AstrBot 里配置一个 **OpenAI 兼容的 Provider**（要能看图，就必须支持图片输入）。
3. 配置 **OneBot 平台适配器**（NapCat / Lagrange 等），由 OneBot 端主动连 AstrBot。
4. 重启 AstrBot，按下面「部署要点」写好本机配置文件。

## 部署要点

本机配置文件（**不要提交进仓库**）：

- `/root/higan/mail.json` —— 邮箱 OAuth 配置
  ```json
  {"host": "outlook.office365.com", "port": 993,
   "user": "<邮箱>", "password": "<密码>", "client_id": "<client_id>",
   "refresh_token": "<refresh_token>"}
  ```
- `/root/higan/llm.json` —— 思考强度与输出上限
  ```json
  {"reasoning_effort": "high", "max_tokens": 4096}
  ```

虚拟机侧是另一个服务（默认 `127.0.0.1:18003`），提供截图 / 点击 / 浏览 / 执行 / 邮件 /
标签页等 HTTP 端点。OneBot（NapCat）方面：插件通过 AstrBot 已连接的客户端调
`call_action`；NapCat 自己的 HTTP 口只用来发消息。

## 状态（实测）

- 工具可执行：VM 13 项 + 标签页 + 点击两段式 + 媒体 + 邮件 + 历史检索 + 任务
- 全链路验证过：截图→喂回模型→作答、点击预览→确认真点、发文字、发图片、读邮件、读群文件
- 已知限制：NapCat 的 HTTP 口对 `get_*` 类 action 返回空对象（空壳），查询类只能走 OneBot 客户端

## License

MIT
