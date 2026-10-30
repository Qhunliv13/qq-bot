# Windows-only presentation adapter. Installation stays in windows-setup.ps1 / bootstrap.py.
# UTF-8 BOM is required because install.cmd invokes Windows PowerShell 5.1.
$script:SetupUiCancelled = $false

function Initialize-SetupUi {
    <# Load the built-in desktop UI only when requested; Python/WSL are not required. #>
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    [System.Windows.Forms.Application]::EnableVisualStyles()
}

function Show-SetupUiWelcome {
    <# Explain the local full Bot deployment before probing WSL or changing this computer. #>
    Initialize-SetupUi
    $text = "欢迎使用 Bot 安装向导！`r`n`r`n不用写代码，接下来会分三步完成：`r`n1. 检查电脑环境；缺少组件时先询问您。`r`n2. 在浏览器填写模型和机器人设置。`r`n3. 由您确认后安装、启动完整 Bot，并检查是否可用。`r`n`r`nWSL2：Windows 内运行 Linux 的环境；安装可能需要管理员批准和重启。`r`nPython：程序运行工具，必须装在 Linux 环境内，Windows 里的 Python 不能替代。`r`nDocker / Compose：运行完整 Bot 的工具；也可使用已开启 WSL 集成的 Docker Desktop。`r`n`r`n不会自动修改生产环境、迁移旧数据或登录 QQ。已有高级命令保持可用。`r`n密钥只在后续本机网页填写，不要把密钥或密码发到聊天、截图或日志中。`r`n安装期间请保留启动窗口；点击「确定」仅开始检查，不代表同意安装组件。"
    $answer = [System.Windows.Forms.MessageBox]::Show($text, 'Bot 安装向导 · 欢迎', 'OKCancel', 'Information', 'Button1')
    $script:SetupUiCancelled = ($answer -ne [System.Windows.Forms.DialogResult]::OK)
    return (-not $script:SetupUiCancelled)
}

function Confirm-SetupUiAction {
    <# Translate the two existing install-consent steps; unknown actions must fail closed. #>
    param([string] $Message)
    if ($Message.StartsWith('Install WSL2 and Ubuntu', [StringComparison]::Ordinal)) {
        # Installing Windows features is separate from merely opening the welcome dialog.
        $text = "还没有可用于安装 Bot 的 Linux 环境。`r`n`r`n是否通过微软 WSL 安装 WSL2 和 Ubuntu？`r`nWindows 可能要求管理员批准、联网下载和重启。不会转换、注销或删除已有发行版。`r`n`r`n安装请求完成不等于环境已就绪。之后请按 Windows 提示重启，从开始菜单打开 Ubuntu 完成首次初始化，再双击 install.cmd。`r`n`r`n点「否」可安全退出，不安装任何组件。"
    } elseif ($Message.StartsWith('Allow these prerequisite changes', [StringComparison]::Ordinal)) {
        # Root package work still uses the existing signed-repository prerequisite helper.
        $text = "选定的 Linux 环境还缺少 Python 3.10+、Docker / Compose，或 Docker 尚未启动。`r`n`r`n允许现有安装助手在该环境中补齐所需组件并启动 Docker 吗？`r`n将使用该 Linux 环境已配置的软件源（推荐 Ubuntu / apt 系发行版），不会替换现有 Docker，也不改 DNS、镜像源或生产配置。需要联网，可能耗时几分钟。`r`n`r`n如果已安装 Docker Desktop，可点「否」，先打开它，并在 Settings → Resources → WSL Integration 启用所选发行版，再重试。`r`n`r`n此确认只安装运行工具；完整 Bot 仍需在后续网页中单独确认部署。"
    } else {
        # Never silently grant permission when a new installation action lacks a GUI explanation.
        throw 'Unsupported graphical consent stage.'
    }
    $answer = [System.Windows.Forms.MessageBox]::Show($text, 'Bot 安装向导 · 安装确认', 'YesNo', 'Question', 'Button2')
    $script:SetupUiCancelled = ($answer -ne [System.Windows.Forms.DialogResult]::Yes)
    return (-not $script:SetupUiCancelled)
}

function Select-SetupUiDistribution {
    <# Choose an already-filtered WSL target with a noneditable desktop list, never terminal input. #>
    param([string[]] $Distributions)
    $form = New-Object System.Windows.Forms.Form
    try {
        $form.Text = 'Bot 安装向导 · 选择 Linux 环境'
        $form.ClientSize = New-Object System.Drawing.Size(560, 210)
        $form.StartPosition = 'CenterScreen'
        $form.FormBorderStyle = 'FixedDialog'
        $form.MaximizeBox = $false
        $form.MinimizeBox = $false
        $label = New-Object System.Windows.Forms.Label
        $label.Location = New-Object System.Drawing.Point(20, 18)
        $label.Size = New-Object System.Drawing.Size(520, 60)
        $label.Text = "您的电脑上有多个 Linux 环境。请选择用于安装完整 Bot 的环境。`r`n推荐 Ubuntu。已有设置和数据会保留，不会更改默认 Linux 用户。"
        $choices = New-Object System.Windows.Forms.ComboBox
        $choices.Location = New-Object System.Drawing.Point(20, 85)
        $choices.Size = New-Object System.Drawing.Size(520, 28)
        $choices.DropDownStyle = 'DropDownList'
        foreach ($distribution in $Distributions) { [void] $choices.Items.Add($distribution) }
        $choices.SelectedIndex = 0
        $confirm = New-Object System.Windows.Forms.Button
        $confirm.Text = '使用此环境'
        $confirm.Location = New-Object System.Drawing.Point(320, 150)
        $confirm.Size = New-Object System.Drawing.Size(110, 32)
        $confirm.DialogResult = 'OK'
        $cancel = New-Object System.Windows.Forms.Button
        $cancel.Text = '取消'
        $cancel.Location = New-Object System.Drawing.Point(440, 150)
        $cancel.Size = New-Object System.Drawing.Size(100, 32)
        $cancel.DialogResult = 'Cancel'
        $form.AcceptButton = $confirm
        $form.CancelButton = $cancel
        $form.Controls.AddRange(@($label, $choices, $confirm, $cancel))
        if ($form.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
            # Closing the window must stop before any Linux root/package operation.
            $script:SetupUiCancelled = $true
            return -1
        }
        return $choices.SelectedIndex
    } finally {
        # Dispose native UI resources so repeated runs do not leave hidden windows behind.
        $form.Dispose()
    }
}

function Show-SetupUiOutcome {
    <# Show a safe Chinese next step; never render raw errors, command lines or capability URLs. #>
    param([int] $ExitCode)
    if ($ExitCode -eq 0) {
        $text = '本机安装向导已关闭。请以网页中的检查结果为准；关闭向导不等于完整 Bot 已部署成功。以后仍可双击 install.cmd 继续配置或检查。'
        $icon = 'Information'
    } elseif ($ExitCode -eq 3) {
        $text = "已提交 WSL / Ubuntu 安装请求，尚未确认可用。`r`n请按 Windows 提示完成重启，然后从开始菜单打开 Ubuntu，完成首次用户名和密码设置。之后再次双击 install.cmd。`r`n请不要把 Linux 密码发到聊天或截图中。"
        $icon = 'Information'
    } elseif ($script:SetupUiCancelled) {
        $text = '已取消当前步骤，不会继续安装组件或启动 Bot。准备好后可再次双击 install.cmd。'
        $icon = 'Information'
    } else {
        $text = "环境检查或启动向导尚未完成，没有宣称部署成功。`r`n`r`n请确认：`r`n• WSL2 / Ubuntu 已安装，且 Ubuntu 首次初始化已完成。`r`n• 若有多个 Linux 环境，请选择 WSL2；本向导不会自动转换 WSL1。`r`n• 需要的 Python 3.10+ 位于所选 Linux 环境内。`r`n• 若使用 Docker Desktop，已打开并启用所选环境的 WSL Integration。`r`n• 使用系统默认浏览器打开本机网页；浏览器异常时请修复后重试。`r`n`r`n修复后双击 install.cmd 重试。可查看启动窗口的环境说明，但不要分享密钥或密码。"
        $icon = 'Warning'
    }
    [void] [System.Windows.Forms.MessageBox]::Show($text, 'Bot 安装向导 · 下一步', 'OK', $icon)
}
