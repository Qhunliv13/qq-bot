# Windows 原生部署复审修复（2026-10-31）

审查基线：`a384712`。本页记录该次审查的六项主要问题与四项边界修复，不覆盖或改写之前真实首装的成功/失败记录。源码仍复用单一完整 AstrBot + 插件实现；无 vendor 修改、全局安装、生产操作或 QQ 账号复用。

## 修复矩阵

| 审查项 | 修改 | 回归/边界 |
|---|---|---|
| A1 中文编码 | 原生 CLI UTF-8 收发，stdin 兼容一个传输 BOM；PowerShell 5.1 调用范围设置并恢复编码 | GBK/cp936、cp1252、UTF-8、中文/emoji、英文输入→中文回复；真实 PS5.1 native 字节哨兵含无窗口/非零退出/启动失败。不把正文放 argv |
| A2 endpoint 首次失败残留 | O_EXCL 创建后从仍持有的 fd 捕获身份；保护/写/flush/close 失败只回滚同一个未发布文件，成功返回原身份 | 真实临时文件 + inert server 故障注入；foreign replacement/reparse 不删除。无法取得身份、持久 close/unlink 失败时仍 fail-closed，不猜测归属 |
| A3 HTTP 滴流无总截止 | 每次 HTTPS 下载独立 monotonic 预算，默认 300s，底层 raw 接收动态缩减 timeout | 真实 HTTPResponse 的 length/chunk/header/chunk-header 离线滴流、TCP 多地址/CONNECT。无下载线程/全局 opener/自动重试；OS DNS 不可中断，但超额结果返回后拒绝 |
| A4 venv 完成标记失真 | 实际 venv 解释器在 `-I -S -B` 下检查适用 roots/extras/markers/版本/Requires-Dist 闭包及记录的导入入口存在性 | 空 venv/缺包/坏 metadata/parser/版本/extra/传递依赖失败；健康旧 schema1 无 pip 升 schema2，不绑定移动路径。Windows 探针复用有界 Job，清理不确定保留 review guard |
| A5 单引号路径 | 共享外部路径校验保留；仅真正 Compose env 序列化保留 quote 限制 | 原生合法 apostrophe 路径允许，Compose 仍拒绝；不硬编码改用户目录 |
| A6 init 假成功 | 已有 marker 的 init 必须 load_install 验证，坏状态不提示补 Key、不重置 | 损坏 marker、缺/坏 settings、name 不匹配均失败；正确安装重复 init 保持文件字节 |
| B1 NoBrowser 不可用 | 显式手动入口显示一个仅本机完整 URL；默认自动打开仍隐藏能力标记 | 重复/畸形 marker 不泄漏；自动浏览器失败给固定手动操作提示，不输出原始异常 |
| B2 cfg 歧义 | venv 关键字段唯一，严格版本格式和 false 隔离值 | false/true 任序重复、重复版本拒绝，不执行 cfg 中 home/command |
| B3 main 失败未收尾 | host supervisor 调已有 runtime.stop()，排空 local/worker/执行锁后 terminate 第一方插件，再传播 main 结果 | main 返回和异常均证明 accepted task 完成且未取消；正常认证 stop 仍 drain 后交上游取消，不修改 vendor |
| B4 JSON/密码组合 | Python 与 PowerShell 入口早拒绝 JSON + ShowPassword | 在状态读取/解释器发现前拒绝；单独显式 ShowPassword 仍可用，不把原契约差异夸成越权 |

主要实现：[native_runtime](../tools/native_runtime.py)、[native_assets](../tools/native_assets.py)、[本地端点](../plugin/local_runtime.py)、[PowerShell](../tools/windows-native.ps1)、[共享路径校验](../tools/bootstrap.py)、[向导](../tools/setup_wizard.py)。

## 定向验证

- 原生 CLI / bootstrap / 向导首批 **265 passed**；随后 BOM 与其他新增用例进入最终全仓，计数不应简单相加。
- 原有与新增端点测试 **51 passed、2 skipped**，无实际 host/network/ACL 修改。
- PowerShell 启动器 **74 passed**；使用真实 Windows PowerShell 5.1 的中文/emoji stdin/stdout/stderr 字节哨兵、编码恢复、退出0/19、CREATE_NO_WINDOW、不可执行入口，以及安全手动 URL。
- 资产测试 **104 passed、4 skipped**；包括标准库真实空 venv、实际 metadata 探针和真实 HTTPResponse 虚拟滴流，不重复公网整套下载。
- 只读检查真实已验收 Python 3.12 venv 的完整适用依赖探针成功，约 0.704s（单探针；不将整资源哈希耗时算作该耗时）。最终有界 Job 探针/资产检查和一次官方 PyPI JSON GET 再验证成功（job `pwsh-38b18fafefea47d0`），未下载安装包或 wheel。

## 新隔离完整宿主

Job `pwsh-e73168b48ab047c1` exit 0：

- 新私有测试目录仅复制已验收的不可变核心/前端/venv，不复制旧模型配置、插件状态、历史或缓存，不重复首次网络安装。
- 使用修复后的第一方代码、全量依赖和新合成本地提供方设置，真实 Dashboard HTML/初始密码 API 登录、认证 health 通过。
- 真实 native CLI 在 `PYTHONIOENCODING=cp936` 的管道里输入中文/emoji，得到页面无关的实际完整回路 UTF-8 中文回复。
- 恰好 **两次 localhost 合成模型请求**；第二次在途保持时发认证 stop，OS HANDLE 证明未提前退出，释放后有可见回复、随后实际退出。
- 同持久状态重启得到新宿主，独立 CLI 父进程退出后仍响应，再认证 stop 并由 OS HANDLE 确认退出；端点不残留。
- 无外部模型/QQ。不能外推干净机重新下载或真实账号。

Job `pwsh-279bd5995ad445da` exit 0：真实 PowerShell 5.1 `wizard -NoBrowser` 使用新 apostrophe + 中文 + 空格 DataDir，手动 URL 仅保存在内存，认证欢迎页通过；未填表不建状态、不安装、不打开浏览器/宿主，精确持有并确认的空闲测试向导已结束。

最终停止检查（job `pwsh-0e52fad5b1ee4042`）：修复实例、此前 Python 3.12 首装实例及 Python 3.13 实例资产仍就绪，端点缺失、Dashboard 不监听；9 份 UTF-8 文档/430 相对链接无错误。正常检查不安装、不修复既有目录。

## 最终回归

首次复审修复历史基线：Job `pwsh-e4231f61ac314153` exit 0，**5,527 passed、50 skipped、460 subtests passed，406.56s**；全范围 pyflakes、发布扫描 **156 文件、0 failures**、diff 检查通过。相比先前 5,424/50/460 新增 **103 项**回归；跳过项不算验收。

## 第二次复审修复（基线 429eca2）

| 审查项 | 修复策略 | 验证范围 |
|---|---|---|
| C1 背景视觉排空遗漏 | replacement 与 native 自身排空共用按实例归属规则；在途视觉／工具／发送先完成，仅取消已识别空闲循环；停止后不启动下一个待描述项目 | 单元回归覆盖 normal stop／main 返回／异常、idle loop、active model、caller self-wait 与旧版已加载 owner 兼容；实际完整宿主在途视觉保存后退出通过（下述独立验收） |
| C2 探针消耗 stdin | 版本发现用 `-I -S -B` 的 stdlib 启动、私有 NUL stdin/stdout/stderr；实际 runtime 的环境和聊天 stdin 契约不变 | 真实 PS5.1 的 startup customizer 读输入场景，不运行真实模型 |
| C3 探针无截止 | 每候选独立 5s wall-clock，另有 1s 归属清理；Win10 Job 原子绑定覆盖 py/venv launcher 后代，结果只用固定版本退出码 | lazy Add-Type 不在子进程预算内；无法确认清理时停止整个发现，不继续下一个候选或 winget；不按进程名杀服务 |
| C4 地址族创建失败 | socket 构造也纳入逐地址 try，失败继续后续候选，仍共用原 monotonic 下载预算 | 首个 IPv6 构造失败→IPv4 可用、全部构造失败及既有 connect/DNS/deadline；106 passed、4 skipped |
| C5 字符／字节限制不一致 | UI 与实际 client 共用 JSONL 编码器和16 KiB限制，按目标部署选择 Windows认证外壳或Unix请求；Thread/busy之前明确拒绝，不截断不重试 | 中文／emoji／转义字符、native/container 边界；保留8000字符表单限制与协议安全限额 |
| C6 失败试聊保留旧回复 | 新操作清空 answer，空状态在页面清空并隐藏旧显示 | 成功→等待→失败的真实隔离浏览器回归；不把上次结果冒充本次回复 |

本轮仍不修改 vendor/global/生产/QQ，新增验证仅使用新私有 fixture 和合成 localhost 提供方，不重复完整资源网络安装。

**二审最终全仓回归**（job `pwsh-6b4742ac1a594159` exit0）：**5,589 passed、50 skipped、460 subtests passed，430.76s**；比429eca2基线新增62项，全范围 pyflakes、发布扫描 **156文件／0 failures**、diff检查通过。专项计数有重叠，不相加。

**二审实际完整宿主**（job `pwsh-b1f24ddfe9694f31` exit0）：新私有目录仅复用已验收不可变 venv／核心／前端；正常播种、新设置、面板登录和实际认证 health 后，通过现有上游插件 reload 入口触发两张合成图片待描述。localhost 提供方保持第一条实际视觉请求，认证 stop 后 OS HANDLE 证明尚未退出；释放后描述写入 manifest，第二张不再发起，随后精确进程退出、端点缺失。恰好 **1次本地合成视觉请求**，不调用外部模型／QQ，不重新下载或安装包。

保留两次驱动准备失败作为边界证据，未改产品去绕过：（1）在首次播种前加入图片使数据非空，产品按预期拒绝接管，宿主未启动；（2）按正常播种后初次 plugin initialize 早于 provider 就绪，图片走无提供方回退，未达到在途条件，该实例认证停止。最终第三次新目录通过已就绪 Dashboard 的正常 reload 入口建立真实视觉在途，不改 vendor 加载顺序。单元覆盖 main返回／异常分支；完整宿主验收只证明正常认证 stop 分支。

交付停止核查（job `pwsh-55209805b0234a96`）：4份本轮文档UTF-8、302条相对链接有效；新修复实例资产仍ready，端点／review guard／pip缓存缺失，Dashboard不监听；两次未通过驱动的自有目录均无端点／Dashboard监听。未删样本或用强杀替代认证排空。

## 使用与剩余边界

- 下载预算用当前会话 `BOT_NATIVE_DOWNLOAD_TIMEOUT`，允许 1–3600 秒整数；安装子进程仍独立使用 `BOT_NATIVE_SETUP_TIMEOUT`，默认每步 1800 秒，不是运行中模型超时。
- `-NoBrowser` 是用户主动选择在当前终端显示私有授权入口；不要分享/截图/写公共日志。默认入口不显示。
- 正常资产探针不写文件；仅不确定 Job 清理时记录已有 `setup-review-required.json` 安全屏障，不删标记绕过。
- **已有插件副本不会自动升级或覆盖。** 这些修复已进入源码；现有运行实例应先备份私有数据、确认停止并按显式迁移流程更新，不能绕过源码/插件一致性检查。
- DNS 解析不可硬中断；依赖元数据/入口存在检查不是全部 RECORD 哈希、二进制 ABI 或第三方包安全审计；网络与外部依赖仍有独立条件。
- 未验收：缺 Python 新机 winget、Explorer/MOTW/SmartScreen、企业策略、真实提供方/QQ/外部工具、Dashboard 全页、整终端关闭/注销。没有因此扩权或使用真实凭据。
