# 一键安装：内嵌 AstrBot，QQ 外置可选

## 当前生产状态与这份发行包的区别

当前生产代码为 `7af432c`：原 AstrBot 宿主、外置 NapCat、原个人 QQ 登录，
本地完整入口和观察上下文减负已部署。该安装器**不更新、不接管现有生产目录或容器**。
本次发行包固定内嵌 AstrBot v4.28.1 原始源码；QQ 原生独立重写仍处于实验阶段，
未提供可登录的替代后端，不能作为开箱默认接入。

## 全新安装

要求：Linux、Python 3.12+、Docker Engine 与 Compose v2；首次构建还需要联网获取
基础镜像、Python 依赖和前端依赖。AstrBot 后端与前端源码已在仓库内，不从运行时下载
不匹配的 UI。Python 上游此版本没有依赖锁，因此不宣称构建完全离线或逐字节可复现。

仓库根目录执行一条命令：

```bash
python3 tools/bootstrap.py up
```

首次交互输入模型兼容端点、模型名、API Key，以及可选的 QQ 管理员 ID。
API Key 使用隐藏输入；没有预设供应商、模型或 QQ 账号。之后创建隔离数据目录，
检查 Docker 和容器归属，再构建并启动。默认面板仅绑定本机 `127.0.0.1:6185`。
原始上游声明和许可文件在 `vendor/astrbot` 及构建镜像中完整保留，
请先阅读 `THIRD_PARTY_NOTICES.md` 和上游原文。

常用命令：

```bash
python3 tools/bootstrap.py doctor
python3 tools/bootstrap.py status
python3 tools/bootstrap.py logs
```

可用 `--data-dir` 选择仓库之外的持久目录，用 `--runtime-name` 选择隔离项目名。
使用相同目录重复运行不会重写密钥、人格、记忆或配置；陌生的非空目录或同名但不属于
该安装的容器会被拒绝，而不是覆盖。未提供自动迁移、停止或删除旧实例的命令。
旧实例占用 6185 时，先在外置 `compose.env` 中设置另一个 `BOT_WEB_PORT`，不要停止旧实例。

默认数据目录由命令帮助列出；其中 `settings.json` 保存模型凭据、初始人格、管理员和
随机生成的面板密码，权限为 0600；目录为 0700。面板用户名为 `astrbot`，密码查看这份
**私有本地文件**的 `dashboard_password` 字段，不写进仓库或命令行。数据子目录
`astrbot` 与 `higan`分别保存原 AstrBot 与插件状态，升级前必须备份二者及私有配置。
初次没有提供人格时保持空文本，不注入强制人设；可通过 `init --persona-file` 提供原文。

容器启动时拒绝悄悄替换已有不同版本插件。后续代码升级须走备份／受控更新流程，
不是借由重复 `up` 强制覆盖持久状态。

## 不连接 QQ 也能用

安装默认没有启用 QQ 适配器；本地 Unix socket 健康检查不依赖 QQ，也不调用模型。
通过现有 bot 实例的本地入口调用完整人格、工具、记忆和任务：

```bash
python3 tools/bootstrap.py health
python3 tools/bootstrap.py chat --prompt '你的请求'
```

这两个命令会进入同一隔离项目的 bot 容器，调用镜像内已有客户端，不另建模型循环；
也可省略 `--prompt` 从标准输入读取。
本地入口使用 Linux root peer credentials，结果写入原历史，不伪造 QQ 送达。
虚拟机、邮件或其他外部工具仍需各自的真实服务与配置；工具 schema 存在不等于已就绪。

## 外接 QQ：不打包 NapCat

1. 从 NapCat／QQ 发布者自行获取、安装并接受其条款；本仓库不下载、分发或启动它们。
2. 在 AstrBot 原面板中添加外部 OneBot v11 反向 WebSocket 适配器，配置监听地址、
   端口和访问令牌；外部 QQ 服务使用同一令牌连接。6199 默认映射仅到宿主 loopback，
   外部容器不能把它误写成自己的 `127.0.0.1`；网络连通方式由操作者明确配置。
3. 需要插件的原生 HTTP QQ 操作时，在外置 `settings.json` 添加：

```json
"onebot": {
  "enabled": true,
  "http_base": "http://你的外部-OneBot-地址:端口",
  "token": "你自行配置的访问令牌"
}
```

这仅为连接信息，不是 NapCat 源码、账号数据或登录迁移。实际能力由外部实现决定。
不要公开发布无鉴权接口、复用他人 QQ 会话，或为一键体验关闭认证。新安装与原生产
可以并存，但不能未经明确安排同时拿同一个 QQ 会话登录。

## 验证边界

本次自动化测试覆盖安装器幂等、外置秘密、拒绝覆盖旧状态、容器归属、源声明哈希、
初始化配置和插件回归。本地环境没有 Docker daemon，未将单元／配置测试冒称为完整
镜像构建、真实扫码登录或生产迁移验收。首次构建失败时使用 `logs`／`doctor` 排查，
不自动切换生产、不删除数据、不降级为不安全连接。
