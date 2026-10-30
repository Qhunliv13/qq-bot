# Double-click setup orchestration; the only bot installer remains bootstrap.py.
# ASCII source intentionally supports Windows PowerShell 5.1 without a UTF-8 BOM.
[CmdletBinding()]
param([switch] $CheckOnly, [switch] $Graphical)
$ErrorActionPreference = 'Stop'
# Only fixed, stage-specific hints reach the user; raw exceptions may contain secrets.
$script:SetupFailureHint = 'Check WSL availability with wsl.exe --list --verbose and retry install.cmd.'
# This adapter defines presentation only; check-only never loads desktop assemblies or opens UI.
if ($Graphical -and -not $CheckOnly) { . (Join-Path $PSScriptRoot 'windows-setup-ui.ps1') }

function Confirm-SetupAction {
    <#
    .SYNOPSIS
    Require explicit consent before installing packages or starting services.
    .OUTPUTS
    Boolean. Only y grants consent; empty input or EOF refuses safely.
    #>
    param([string] $Message)
    # Graphical consent is still explicit; CLI and check-only retain their original semantics.
    if ($Graphical -and -not $CheckOnly) { return (Confirm-SetupUiAction $Message) }
    # EOF is a refusal, not a null-method exception or implicit install approval.
    $answer = [string] (Read-Host ($Message + ' [y/N]'))
    return ($answer.Trim() -ieq 'y')
}

function Get-SetupDistributions {
    <#
    .SYNOPSIS
    Strip WSL UTF-16 NULs without selecting Docker Desktop internal distributions.
    .OUTPUTS
    User distribution identifiers supported by the shared Windows launcher.
    #>
    param([string[]] $Lines)
    return @($Lines | ForEach-Object { ($_ -replace "`0", '').Trim() } | Where-Object {
        $_ -match '^[A-Za-z0-9][A-Za-z0-9_.-]*$' -and $_ -notmatch '^docker-desktop($|-)'
    })
}

function Get-SetupDistroIndex {
    <#
    .SYNOPSIS
    Validate menu input without evaluating expressions or accepting command text.
    .OUTPUTS
    Zero-based index, or -1 for cancellation. Invalid nonempty input throws.
    #>
    param([string] $Value, [int] $Count)
    # Empty/EOF and q deliberately cancel before any root command or install prompt.
    if ([string]::IsNullOrWhiteSpace($Value) -or $Value.Trim() -ieq 'q') { return -1 }
    $index = 0
    if (-not [int]::TryParse($Value, [ref] $index) -or $index -lt 1 -or $index -gt $Count) {
        $script:SetupFailureHint = 'Select a displayed WSL distribution number, or press Enter / q to cancel. Run install.cmd again to retry.'
        throw 'Select a valid distribution number.'
    }
    return ($index - 1)
}

function Install-SetupWsl {
    <#
    .SYNOPSIS
    Request Microsoft WSL installation without rebooting or changing existing distros.
    .OUTPUTS
    3 when installation was requested but is not verified ready; otherwise 2.
    #>
    if (-not (Confirm-SetupAction 'Install WSL2 and Ubuntu using Microsoft WSL? Administrator approval and a reboot may be required.')) {
        return 2
    }
    $script:SetupFailureHint = 'WSL installation could not be requested. Install WSL2 with Ubuntu using Windows WSL, complete its first-run prompts, then retry install.cmd.'
    $process = Start-Process -FilePath "$env:SystemRoot\System32\wsl.exe" -ArgumentList @('--install', '--distribution', 'Ubuntu', '--no-launch') -Verb RunAs -Wait -PassThru
    # A failed request is not an installed or running distribution; return failure.
    if ($process.ExitCode -notin @(0, 3010)) {
        Write-Host 'WSL installation request failed. This Windows WSL version may not support --install / --no-launch; check wsl.exe --help and install WSL2 with Ubuntu manually.'
        return 2
    }
    Write-Host 'WSL installation requested, not verified ready. Complete any Windows reboot/Ubuntu first-run prompts, then double-click install.cmd again.'
    return 3
}

function Invoke-WindowsSetup {
    <#
    .SYNOPSIS
    Detect prerequisites, preserve default-user state, and invoke one shared core.
    .DESCRIPTION
    CheckOnly never installs or launches. Interactive changes require consent;
    a stopped WSL2 distribution is supported without assuming Docker is ready.
    #>
    # Load WinForms only for an explicit GUI run, never for side-effect-free checks/tests.
    if ($Graphical -and -not $CheckOnly) {
        if (-not (Show-SetupUiWelcome)) { return 2 }
    }
    Write-Host 'Qhunliv Bot - Windows one-click setup'
    Write-Host 'Full runtime; no production migration, QQ login or NapCat installation.'
    Write-Host 'Model API keys are requested privately by the shared installer.'
    $repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).ProviderPath
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        Write-Host 'WSL command is missing. Enable WSL2 and install Ubuntu using Windows WSL, then complete its first-run prompts. Windows Python or Docker alone is not enough.'
        if ($CheckOnly) { return 2 }
        return (Install-SetupWsl)
    }
    $script:SetupFailureHint = 'Cannot list WSL distributions. Run wsl.exe --list --verbose and wsl.exe --help; enable or update WSL if --quiet / --verbose is unsupported.'
    $raw = @(& wsl.exe --list --quiet 2>$null)
    $listCode = $LASTEXITCODE
    # A command/flag failure must not be mistaken for an empty, uninstalled WSL.
    if ($listCode -ne 0) { Write-Host $script:SetupFailureHint; return 2 }
    $distros = @(Get-SetupDistributions $raw)
    if ($distros.Count -eq 0) {
        Write-Host 'No usable user WSL distribution was found; Docker Desktop internal distributions are not setup targets.'
        if ($CheckOnly) { return 2 }
        return (Install-SetupWsl)
    }
    $verbose = @(& wsl.exe --list --verbose 2>$null)
    if ($LASTEXITCODE -ne 0) { Write-Host $script:SetupFailureHint; return 2 }
    $distro = $distros[0]
    if ($distros.Count -gt 1) {
        for ($i = 0; $i -lt $distros.Count; $i++) { Write-Host ('{0}. {1}' -f ($i + 1), $distros[$i]) }
        if ($CheckOnly) { Write-Host 'Multiple distributions: interactive selection required.'; return 2 }
        $script:SetupFailureHint = 'WSL selection was interrupted; no prerequisites were changed. Run install.cmd again and select a displayed number, or press Enter / q to cancel.'
        if ($Graphical) { $distroIndex = Select-SetupUiDistribution $distros }
        else { $distroIndex = Get-SetupDistroIndex (Read-Host 'Select the WSL distribution to use (Enter / q cancels)') $distros.Count }
        if ($distroIndex -lt 0) { Write-Host 'Setup cancelled. No prerequisites were changed or runtime started.'; return 2 }
        $distro = $distros[$distroIndex]
    }
    $isWsl2 = @($verbose | Where-Object { (($_ -replace "`0", '') -replace '^\s*\*?\s*', '') -match ('^' + [regex]::Escape($distro) + '\s+.*\s+2\s*$') }).Count -eq 1
    if (-not $isWsl2) { Write-Host 'Selected distribution is not confirmed as WSL2. No automatic conversion or shutdown is performed.'; return 2 }
    Write-Host ('Using WSL2: ' + $distro)
    # Keep the existing default user's external data path even though setup uses root.
    # Do not change the distribution's default user or adopt a production directory.
    $script:SetupFailureHint = 'Cannot read the selected WSL default user home. Complete the distribution first-run prompts and check wsl.exe --distribution <name> --exec printenv HOME.'
    $homeLines = @(& wsl.exe --distribution $distro --exec printenv HOME)
    if ($LASTEXITCODE -ne 0 -or $homeLines.Count -ne 1) { throw 'WSL home unavailable.' }
    $linuxHome = [string] $homeLines[0]
    # HOME is one native argv value, not shell source; Unicode/spaces are supported.
    # Reject traversal components and the same unsupported metacharacters as the launcher.
    if (-not $linuxHome.StartsWith('/') -or $linuxHome -match '[\x00-\x1f\x7f"''`$;&|<>]' -or ($linuxHome -split '/') -contains '..') { throw 'Unsupported WSL home.' }
    $script:SetupFailureHint = 'Cannot access this checkout in WSL. The selected distribution needs wslpath and access to this Windows drive; Chinese names and spaces are supported.'
    $repoLines = @(& wsl.exe --distribution $distro --exec wslpath -a -u $repo)
    if ($LASTEXITCODE -ne 0 -or $repoLines.Count -ne 1) { throw 'Repository path unavailable in WSL.' }
    $linuxRepo = [string] $repoLines[0]
    # Existing launcher applies the same argument-safety validation before bootstrap.
    if (-not $linuxRepo.StartsWith('/') -or $linuxRepo -match '[\x00-\x1f\x7f"''`$;&|<>]') { throw 'Unsupported WSL checkout path.' }
    $prerequisites = $linuxRepo + '/tools/windows-prerequisites.sh'
    $script:SetupFailureHint = 'Cannot check Linux prerequisites. The selected WSL distribution needs bash and wslpath; use Ubuntu or provide Python 3.10+, Docker and Compose v2 manually.'
    & wsl.exe --distribution $distro --user root --exec bash $prerequisites --check | Out-Host
    $checkCode = $LASTEXITCODE
    # Preserve helper status codes in check-only mode; hints are advice, not fixes.
    switch ($checkCode) {
        10 { Write-Host 'Python 3.10+ is required inside the selected WSL distribution, not only on Windows.' }
        11 { Write-Host 'Docker / Compose v2 is unavailable in this WSL distribution. If using Docker Desktop, start it and enable Settings > Resources > WSL Integration for the selected distribution.' }
        12 { Write-Host 'Docker is installed but its daemon is not reachable. Start Docker Desktop and enable its WSL Integration, or start the existing Linux Docker service. This check has not started a runtime.' }
    }
    # Missing bash, unsupported flags and unexpected helper failures are not install requests.
    if ($checkCode -notin @(0, 10, 11, 12)) {
        Write-Host $script:SetupFailureHint
        if ($CheckOnly) { return $checkCode }
        return 2
    }
    if ($CheckOnly) { return $checkCode }
    if ($checkCode -ne 0) {
        Write-Host 'Automatic package installation requires an apt-based distribution with suitable signed packages (Ubuntu is recommended). Other distributions need prerequisites installed manually.'
        Write-Host 'Setup can install missing Python/Docker/Compose from the configured signed Linux repositories and start Docker.'
        Write-Host 'Existing Docker installations are not replaced. No DNS, registry mirror or production setting is changed.'
        if (-not (Confirm-SetupAction 'Allow these prerequisite changes in the selected WSL distribution?')) { return 2 }
        & wsl.exe --distribution $distro --user root --exec bash $prerequisites --install | Out-Host
        if ($LASTEXITCODE -ne 0) { Write-Host 'Prerequisites are not ready. Resolve the reported prerequisite and double-click install.cmd again.'; return 2 }
    }
    $script:SetupFailureHint = 'The shared installer did not complete. Check its prerequisite diagnostics and retry install.cmd; no successful deployment is claimed.'
    $dataDir = $linuxHome.TrimEnd('/') + '/.local/share/qhunliv-bot'
    if ($Graphical) {
        # All configuration and full-runtime deployment stays in the shared Python browser wizard.
        # This branch deliberately never invokes up or asks for a model/API key in the terminal.
        Write-Host 'Opening the local browser setup wizard; no runtime is started until confirmed there.'
        & (Join-Path $PSScriptRoot 'windows.ps1') wizard -Distro $distro -User root -DataDir $dataDir | Out-Host
    } else {
        # Preserve the existing advanced CLI flow, including -OpenDashboard compatibility.
        Write-Host 'Starting the shared installer. Existing settings are kept; first run asks for model URL, model ID and a hidden API key.'
        & (Join-Path $PSScriptRoot 'windows.ps1') up -Distro $distro -User root -DataDir $dataDir -OpenDashboard | Out-Host
    }
    $launchCode = $LASTEXITCODE
    if ($launchCode -eq 0 -and -not $Graphical) {
        # Display exact same-instance commands; default distro/user may be different.
        # These contain paths only, not credentials, and are never executed here.
        Write-Host 'Manage this same installation from this checkout in PowerShell:'
        Write-Host ('powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 info -Distro "{0}" -User root -DataDir "{1}"' -f $distro, $dataDir)
        Write-Host 'Add -ShowPassword to info only when you want to reveal the dashboard password locally. Do not share the output.'
        Write-Host 'Use the same -Distro, -User and -DataDir with status, health or stop. stop keeps your data.'
    }
    return $launchCode
}

# Dot-sourcing exposes pure functions for tests without installing or launching anything.
if ($MyInvocation.InvocationName -ne '.') {
    try {
        $setupExitCode = Invoke-WindowsSetup
        if ($Graphical -and -not $CheckOnly) { Show-SetupUiOutcome $setupExitCode }
        exit $setupExitCode
    }
    catch {
        # Fixed diagnostics never leak user-supplied key/arguments or raw exceptions.
        [Console]::Error.WriteLine('Setup did not complete. ' + $script:SetupFailureHint)
        if ($Graphical -and -not $CheckOnly -and (Get-Command Show-SetupUiOutcome -ErrorAction SilentlyContinue)) { Show-SetupUiOutcome 2 }
        exit 2
    }
}
