# Native Windows entry point: all bot/runtime behavior belongs to native_runtime.py.
# Compatible with Windows PowerShell 5.1; no WSL, Docker, elevation or persistent policy changes.
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('wizard', 'init', 'up', 'doctor', 'info', 'status', 'stop', 'logs', 'health', 'chat')]
    [string] $Command = 'wizard',
    [string] $ModelBase,
    [string] $Model,
    [string] $ApiKeyEnv,
    [string] $PersonaFile,
    [string] $DataDir,
    [string] $RuntimeName,
    [ValidateRange(1, 3600)]
    [int] $WaitTimeout,
    [switch] $NonInteractive,
    [switch] $NoBrowser,
    [switch] $ShowPassword,
    [switch] $Json,
    [string[]] $OwnerQQ,
    # Read chat input through stdin only, never a command-line argument or saved transcript.
    [Parameter(ValueFromPipeline = $true, DontShow = $true)]
    [string] $InputLine
)
begin {
    $hasPipelineInput = $MyInvocation.ExpectingInput
    $pipelineInput = @()
}
process {
    if ($hasPipelineInput) { $pipelineInput += $InputLine }
}
end {
function Assert-NativeArgument {
    <# Reject controls and quotes before crossing PowerShell 5.1 native argv quoting boundaries. #>
    param([string] $Value)
    if ([string]::IsNullOrWhiteSpace($Value) -or $Value -match '[\x00-\x1f\x7f"`]' -or $Value.EndsWith('\')) {
        # A trailing backslash before a closing quote is ambiguous in legacy native invocation.
        throw 'Invalid native argument.'
    }
}

function Get-NativePython {
    <# Prefer supported 3.12, accept 3.13, and probe real interpreters instead of trusting executable names. #>
    $candidates = @(
        @{ Name = 'py.exe'; Prefix = @('-3.12') },
        @{ Name = 'py.exe'; Prefix = @('-3.13') },
        @{ Name = 'python.exe'; Prefix = @() },
        @{ Name = 'python3.exe'; Prefix = @() }
    )
    if ($env:LOCALAPPDATA) {
        # winget installs may not update this running process's PATH; do not mutate user/machine PATH.
        foreach ($minor in @('312', '313')) {
            $candidates += @{ Name = (Join-Path $env:LOCALAPPDATA "Programs\Python\Python$minor\python.exe"); Prefix = @() }
        }
    }
    foreach ($candidate in $candidates) {
        try {
            $resolved = Get-Command $candidate.Name -ErrorAction SilentlyContinue
            if (-not $resolved) { continue }
            $executable = if ($resolved.Source) { [string] $resolved.Source } else { [string] $resolved.Name }
            # Store aliases can open an interactive installer; they are not existing interpreters.
            if ($executable -match '(?i)[\\/]Microsoft[\\/]WindowsApps[\\/]') { continue }
            $prefix = @($candidate.Prefix)
            $global:LASTEXITCODE = $null
            # This finite probe reads no stdin and never imports third-party modules.
            $version = @(& $executable @prefix -c 'import sys; print(str(sys.version_info.major) + chr(46) + str(sys.version_info.minor))' 2>$null)
            if ($LASTEXITCODE -eq 0 -and $version.Count -eq 1 -and [string] $version[0] -cmatch '^3\.(12|13)$') {
                return @{ Executable = $executable; Prefix = $prefix }
            }
        } catch {
            # Discovery failures may contain executable paths; fall through without printing them.
            continue
        }
    }
    return $null
}

function Confirm-NativePythonInstall {
    <# Require a deliberate user choice before a user-scope package installation; never request admin rights. #>
    $message = 'Python 3.12 or 3.13 was not found. Install official Python 3.12 for your Windows account with winget? No administrator access is requested.'
    try {
        Add-Type -AssemblyName PresentationFramework -ErrorAction Stop
        # Default No prevents an accidental Enter key from authorizing installation.
        $choice = [System.Windows.MessageBox]::Show($message, 'QhunlivBot native setup', 'YesNo', 'Question', 'No')
        return ([string] $choice -eq 'Yes')
    } catch {
        # Headless hosts may lack desktop assemblies. A closed/redirected console is not consent.
        if ([Console]::IsInputRedirected) { return $false }
        try { return ((Read-Host ($message + ' [y/N]')) -cmatch '^[yY]([eE][sS])?$') }
        catch { return $false }
    }
}

function Install-NativePython {
    <# Invoke only the official exact winget package, user scope, after the caller obtained explicit consent. #>
    param([string] $Winget)
    $global:LASTEXITCODE = $null
    & $Winget install --id Python.Python.3.12 --exact --source winget --scope user --silent --accept-source-agreements --accept-package-agreements --disable-interactivity
    if ($null -eq $LASTEXITCODE -or $LASTEXITCODE -ne 0) { throw 'Python installation failed.' }
}

function Get-NativeSetupBrowserUrl {
    <# Accept only the established exact loopback marker; its capability token must never reach logs. #>
    param([string] $Line)
    $match = [regex]::Match($Line, '\ABOT_SETUP_URL=(http://127\.0\.0\.1:([0-9]{1,5})/#token=[A-Za-z0-9_-]{32,})\z')
    if (-not $match.Success) { return $null }
    $port = [int] $match.Groups[2].Value
    if ($port -lt 1 -or $port -gt 65535) { return $null }
    return $match.Groups[1].Value
}

# Native launcher entry point. Keep failure text fixed: paths, keys and capability tokens are never echoed.
$failureHint = 'Invalid option. Use a supported native command and Windows paths; keys belong in the setup form or a named environment variable.'
try {
    if ($ShowPassword -and $Command -ne 'info') { throw 'ShowPassword requires info.' }
    if ($Json -and $Command -ne 'info') { throw 'Json requires info.' }
    if ($NoBrowser -and $Command -ne 'wizard') { throw 'NoBrowser requires wizard.' }
    $repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).ProviderPath
    $entry = Join-Path $repo 'tools\native_runtime.py'
    if (-not (Test-Path -LiteralPath $entry -PathType Leaf)) {
        $failureHint = 'The native runtime entry point is missing. Restore the complete checkout and retry.'
        throw 'Missing native runtime.'
    }
    # Python owns runtime/config semantics. This layer only validates transport and forwards typed flags.
    $runtimeArgs = @($Command)
    foreach ($pair in @(@('ModelBase', '--model-base'), @('Model', '--model'), @('ApiKeyEnv', '--api-key-env'), @('PersonaFile', '--persona-file'), @('RuntimeName', '--runtime-name'))) {
        if ($PSBoundParameters.ContainsKey($pair[0])) {
            $value = [string] $PSBoundParameters[$pair[0]]
            Assert-NativeArgument $value
            if ($pair[0] -eq 'ApiKeyEnv' -and $value -cnotmatch '^[A-Za-z_][A-Za-z0-9_]*$') { throw 'Invalid environment variable name.' }
            $runtimeArgs += @($pair[1], $value)
        }
    }
    if ($PSBoundParameters.ContainsKey('DataDir')) {
        Assert-NativeArgument $DataDir
    } else {
        # Full never shares the Lite directory or writes mutable state inside the checkout.
        if (-not $env:LOCALAPPDATA) { throw 'LOCALAPPDATA is unavailable.' }
        $DataDir = Join-Path $env:LOCALAPPDATA 'QhunlivBot\Full'
    }
    if ($DataDir -notmatch '^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+[\\/])') { throw 'DataDir must be a Windows absolute path.' }
    $runtimeArgs += @('--data-dir', $DataDir)
    if ($PSBoundParameters.ContainsKey('WaitTimeout')) { $runtimeArgs += @('--wait-timeout', [string] $WaitTimeout) }
    if ($NonInteractive) { $runtimeArgs += '--non-interactive' }
    if ($ShowPassword) { $runtimeArgs += '--show-password' }
    if ($Json) { $runtimeArgs += '--json' }
    foreach ($owner in $OwnerQQ) {
        if ($owner -cnotmatch '^[0-9]{5,20}$') { throw 'Invalid OwnerQQ.' }
        $runtimeArgs += @('--owner-qq', $owner)
    }
    # The launcher consumes the existing marker; never let Python launch a second browser.
    if ($Command -eq 'wizard') { $runtimeArgs += '--no-browser' }

    $failureHint = 'Python 3.12 or 3.13 is required. Install Python 3.12 for your account, then retry; no WSL or Docker is needed.'
    $python = Get-NativePython
    if (-not $python -and -not $NonInteractive -and $Command -in @('wizard', 'init', 'up')) {
        $wingetCommand = Get-Command 'winget.exe' -ErrorAction SilentlyContinue
        if ($wingetCommand -and (Confirm-NativePythonInstall)) {
            $winget = if ($wingetCommand.Source) { [string] $wingetCommand.Source } else { [string] $wingetCommand.Name }
            $failureHint = 'Python installation did not finish. Retry setup, or install official Python 3.12 for your account. Administrator access is not required.'
            Install-NativePython $winget
            $python = Get-NativePython
        }
    }
    if (-not $python) { throw 'Supported Python not found.' }
    $executable = [string] $python.Executable
    $launchArgs = @($python.Prefix) + @('-u', $entry) + $runtimeArgs
    $failureHint = 'Native runtime launch failed. Run doctor with the same DataDir; keep API keys in the setup form or environment, not command arguments.'
    $global:LASTEXITCODE = $null
    if ($Command -eq 'wizard') {
        # Stream a single redirected stdout pipe. Stderr stays inherited, so neither pipe can fill while waiting.
        $browserOpened = $false
        & $executable @launchArgs | ForEach-Object {
            $line = [string] $_
            if ($line.Contains('BOT_SETUP_URL=')) {
                $setupUrl = Get-NativeSetupBrowserUrl $line
                if (-not $setupUrl) {
                    [Console]::Error.WriteLine('Setup returned an invalid local browser address; no browser was opened.')
                } elseif (-not $NoBrowser -and -not $browserOpened) {
                    try {
                        Start-Process -FilePath $setupUrl -ErrorAction Stop | Out-Null
                        $browserOpened = $true
                        Write-Host 'The local setup page is open. Keep this window open while using it.'
                    } catch {
                        [Console]::Error.WriteLine('The setup browser could not open. Check your default Windows browser, close setup and retry.')
                    }
                }
                # Valid, invalid and repeated capability markers are all consumed, never printed.
            } else { Write-Output $line }
        }
    } elseif ($hasPipelineInput) { $pipelineInput | & $executable @launchArgs }
    else { & $executable @launchArgs }
    if ($null -eq $LASTEXITCODE) { throw 'Native runtime did not return an exit code.' }
    exit $LASTEXITCODE
} catch {
    [Console]::Error.WriteLine('Windows native launcher: ' + $failureHint)
    exit 2
}
} # end: preserve a live console or forward pipeline input exactly once
