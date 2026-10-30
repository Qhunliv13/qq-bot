# Double-click setup orchestration; the only bot installer remains bootstrap.py.
# ASCII source intentionally supports Windows PowerShell 5.1 without a UTF-8 BOM.
[CmdletBinding()]
param([switch] $CheckOnly)
$ErrorActionPreference = 'Stop'

function Confirm-SetupAction {
    <# Require explicit consent before installing packages or starting services. #>
    param([string] $Message)
    return ((Read-Host ($Message + ' [y/N]')).Trim() -ieq 'y')
}

function Get-SetupDistributions {
    <# Strip WSL's UTF-16 NULs; never select Docker Desktop's internal distributions. #>
    param([string[]] $Lines)
    return @($Lines | ForEach-Object { ($_ -replace "`0", '').Trim() } | Where-Object {
        $_ -match '^[A-Za-z0-9][A-Za-z0-9_.-]*$' -and $_ -notmatch '^docker-desktop($|-)'
    })
}

function Get-SetupDistroIndex {
    <# Validate menu input without evaluating expressions or accepting command text. #>
    param([string] $Value, [int] $Count)
    $index = 0
    if (-not [int]::TryParse($Value, [ref] $index) -or $index -lt 1 -or $index -gt $Count) {
        throw 'Select a valid distribution number.'
    }
    return ($index - 1)
}

function Install-SetupWsl {
    <# Use Microsoft's installed WSL entry; never reboot or change existing distros. #>
    if (-not (Confirm-SetupAction 'Install WSL2 and Ubuntu using Microsoft WSL? Administrator approval and a reboot may be required.')) {
        return 2
    }
    $process = Start-Process -FilePath "$env:SystemRoot\System32\wsl.exe" -ArgumentList @('--install', '--distribution', 'Ubuntu', '--no-launch') -Verb RunAs -Wait -PassThru
    Write-Host 'WSL installation requested. Complete any Windows reboot/Ubuntu first-run prompts, then double-click install.cmd again.'
    # Do not report deployment success before a required reboot or first-run completes.
    if ($process.ExitCode -notin @(0, 3010)) { return 2 }
    return 3
}

function Invoke-WindowsSetup {
    <# Detect prerequisites, preserve default-user state, and invoke one shared core. #>
    Write-Host 'Qhunliv Bot - Windows one-click setup'
    Write-Host 'Full runtime; no production migration, QQ login or NapCat installation.'
    Write-Host 'Model API keys are requested privately by the shared installer.'
    $repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).ProviderPath
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        if ($CheckOnly) { Write-Host 'WSL is missing.'; return 2 }
        return (Install-SetupWsl)
    }
    $raw = @(& wsl.exe --list --quiet 2>$null)
    $listCode = $LASTEXITCODE
    $distros = @(Get-SetupDistributions $raw)
    if ($listCode -ne 0 -or $distros.Count -eq 0) {
        if ($CheckOnly) { Write-Host 'No usable WSL distribution.'; return 2 }
        return (Install-SetupWsl)
    }
    $verbose = @(& wsl.exe --list --verbose 2>$null)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot determine WSL2 availability.' }
    $distro = $distros[0]
    if ($distros.Count -gt 1) {
        for ($i = 0; $i -lt $distros.Count; $i++) { Write-Host ('{0}. {1}' -f ($i + 1), $distros[$i]) }
        if ($CheckOnly) { Write-Host 'Multiple distributions: interactive selection required.'; return 2 }
        $distro = $distros[(Get-SetupDistroIndex (Read-Host 'Select the WSL distribution to use') $distros.Count)]
    }
    $isWsl2 = @($verbose | Where-Object { (($_ -replace "`0", '') -replace '^\s*\*?\s*', '') -match ('^' + [regex]::Escape($distro) + '\s+.*\s+2\s*$') }).Count -eq 1
    if (-not $isWsl2) { Write-Host 'Selected distribution is not confirmed as WSL2. No automatic conversion or shutdown is performed.'; return 2 }
    Write-Host ('Using WSL2: ' + $distro)
    # Keep the existing default user's external data path even though setup uses root.
    # Do not change the distribution's default user or adopt a production directory.
    $homeLines = @(& wsl.exe --distribution $distro --exec printenv HOME)
    if ($LASTEXITCODE -ne 0 -or $homeLines.Count -ne 1) { throw 'WSL home unavailable.' }
    $linuxHome = [string] $homeLines[0]
    if ($linuxHome -notmatch '^/[A-Za-z0-9_./ -]+$' -or $linuxHome -match '\.\.') { throw 'Unsupported WSL home.' }
    $repoLines = @(& wsl.exe --distribution $distro --exec wslpath -a -u $repo)
    if ($LASTEXITCODE -ne 0 -or $repoLines.Count -ne 1) { throw 'Repository path unavailable in WSL.' }
    $linuxRepo = [string] $repoLines[0]
    # Existing launcher applies the same argument-safety validation before bootstrap.
    if (-not $linuxRepo.StartsWith('/') -or $linuxRepo -match '[\x00-\x1f\x7f"''`$;&|<>]') { throw 'Unsupported WSL checkout path.' }
    $prerequisites = $linuxRepo + '/tools/windows-prerequisites.sh'
    & wsl.exe --distribution $distro --user root --exec bash $prerequisites --check | Out-Host
    $checkCode = $LASTEXITCODE
    if ($CheckOnly) { return $checkCode }
    if ($checkCode -ne 0) {
        Write-Host 'Setup can install missing Python/Docker/Compose from the configured signed Linux repositories and start Docker.'
        Write-Host 'Existing Docker installations are not replaced. No DNS, registry mirror or production setting is changed.'
        if (-not (Confirm-SetupAction 'Allow these prerequisite changes in the selected WSL distribution?')) { return 2 }
        & wsl.exe --distribution $distro --user root --exec bash $prerequisites --install | Out-Host
        if ($LASTEXITCODE -ne 0) { Write-Host 'Prerequisites are not ready. Resolve the reported prerequisite and double-click install.cmd again.'; return 2 }
    }
    Write-Host 'Starting the shared installer. Existing settings are kept; first run asks for model URL, model ID and a hidden API key.'
    & (Join-Path $PSScriptRoot 'windows.ps1') up -Distro $distro -User root -DataDir ($linuxHome.TrimEnd('/') + '/.local/share/qhunliv-bot') -OpenDashboard | Out-Host
    return $LASTEXITCODE
}

# Dot-sourcing exposes pure functions for tests without installing or launching anything.
if ($MyInvocation.InvocationName -ne '.') {
    try { exit (Invoke-WindowsSetup) }
    catch {
        # Fixed diagnostics never leak user-supplied key/arguments or raw exceptions.
        [Console]::Error.WriteLine('Setup did not complete. Check WSL availability and docs/WINDOWS.md; no successful deployment is claimed.')
        exit 2
    }
}
