# Windows 安装与管理

**默认入口已改为原生完整版：无需 WSL / Docker / Node，不是 Lite。**完整解压仓库，双击根目录 [install.cmd](../install.cmd)，以 Windows Python 3.12 / 3.13 运行同一固定 AstrBot 4.28.1 和完整插件。API Key 在同一浏览器部署向导中填写，不放进命令行。

> **2026-10-30 验收边界：**原生实现正在集成，真实 venv 安装仍在进行；原生完整启动、界面、模型、QQ 与优雅停机尚未验收。不要把入口 / ACL / 模拟安装测试当作完整部署成功。详见 [部署验证记录](QUICK_DEPLOY_VERIFICATION.md)。

## 选择入口

| 需要什么 | 使用什么 |
|---|---|
| 默认 Windows 原生完整版，保留工具、记忆与任务 | 双击 [install.cmd](../install.cmd)；[完整部署说明](WINDOWS_NATIVE_FULL.md) |
| 管理已有 WSL / Docker 完整实例，或显式选择兼容路径 | [WSL 兼容指南](WINDOWS_WSL.md)；`install.ps1` / `tools/windows.ps1` / `tools/windows-setup.ps1` 仍保留 |
| 仅原生文字聊天，不含 QQ、工具、持久记忆与任务 | 双击 [lite.cmd](../lite.cmd)；[Lite 指南](WINDOWS_LITE.md) |

WSL 与原生私有状态不共享，不自动迁移；**不要用旧 `install.ps1` 命令管理新的原生实例**。

## 原生五步开始

1. 完整解压后双击 `install.cmd`。已有 Python 3.12 / 3.13 就复用；缺少时仅在你明确同意后可用 winget 安装官方 Python 3.12 到当前 Windows 用户，不请求管理员权限。不安装 WSL / Docker，不做全局 pip 安装。
2. 浏览器打开 **准备好了吗 → 连接模型 → 创建 Bot → 启动与试聊 → 开始使用**。填写自己的 OpenAI 兼容 Base URL、Model ID、API Key，角色和 QQ 所有者可留空。
3. 分别确认保存与启动。首次准备仓库外私有 venv、完整依赖、vendored 核心副本、复制插件及同版本 SHA256 校验的官方预构建 Dashboard；无需本机 Node 构建。
4. 本地健康后再明确确认试聊费用 / 工具执行。健康、模型回复、QQ 登录与实际送达分别判定；QQ / NapCat 仍须外部服务或客户端手动配置，不自动登录。
5. 再次双击管理原实例，不覆盖数据；关闭浏览器 / 安装窗口不是停止命令。detached 子进程与排空停止是当前实现目标，仍待真实 Windows 生命周期验证。

默认私有目录 **`%LOCALAPPDATA%\QhunlivBot\Full`**，新目录限当前用户 SID ACL、无需提权。面板只监听 loopback，默认 **http://127.0.0.1:6185**，首次端口冲突会选空闲端口并保存；用户名 `astrbot`，初始密码随机。不要分享设置、operator 端点文件、授权链接或日志原文。

## 原生日常管理

在仓库根目录运行；自定义安装需每次沿用同一 `-DataDir`（绝对 Windows 路径）与 `-RuntimeName`：

```powershell
.\tools\windows-native.ps1 info                 # 配置地址 / 初始账号，不证明就绪
.\tools\windows-native.ps1 info -ShowPassword   # 仅显式显示初始面板密码，不显示模型 Key
.\tools\windows-native.ps1 doctor               # 原生前置检查，不安装 / 启动
.\tools\windows-native.ps1 status               # 同一实例认证健康
.\tools\windows-native.ps1 health               # 不调用模型 / QQ
.\tools\windows-native.ps1 logs                 # 只给出私有路径，不输出原始日志
'Hello' | .\tools\windows-native.ps1 chat        # 主动完整回路，可能计费 / 执行工具
.\tools\windows-native.ps1 stop                 # 请求排空停止，保留数据
.\tools\windows-native.ps1 up                   # 原数据启动，不自动升级 / 覆盖
```

若系统阻止脚本，使用 `powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File .\tools\windows-native.ps1 info`；仅本次进程放行。无参数默认 `wizard`，其余参数、私有目录布局、日志、停机边界和外部能力依赖见 [原生完整版详细说明](WINDOWS_NATIVE_FULL.md)。

`info` 只显示初始凭据；已在面板改过密码则用新值，不重置它。`stop` 显示 accepted 不是进程已退出证明，有在途请求时等待排空。VM、邮件、浏览器与指针桥接等 Linux 依赖不会自动转成 Windows 宿主工具；保留完整 schema 不等于外部能力已配置或验收。
