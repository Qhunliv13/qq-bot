# Native Windows only: reuse an existing Python; all runtime behavior lives in lite_native.py.
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('web', 'chat', 'info', 'configure')]
    [string] $Command = 'web',
    [string] $DataDir,
    [string] $ModelBase,
    [string] $Model,
    [string] $ApiKeyEnv,
    [string] $PersonaFile,
    # Python owns defaults; only explicitly bound overrides become command-line arguments.
    [ValidateRange(1, 1000)]
    [int] $HistoryTurns,
    [ValidateRange(0, 65535)]
    [int] $Port,
    [switch] $NoBrowser,
    [switch] $NonInteractive,
    # Pipeline chat is private stdin, never a prompt argument or a second command.
    [Parameter(ValueFromPipeline = $true, DontShow = $true)]
    [string] $InputLine
)
begin {
    $hasPipelineInput = $MyInvocation.ExpectingInput
    $pipelineInput = New-Object 'System.Collections.Generic.List[string]'
}
process {
    # Retain blank lines and their order; Python owns chat parsing and EOF behavior.
    if ($hasPipelineInput) { $pipelineInput.Add($InputLine) }
}
end {
function Assert-LiteArgument {
    <# Reject empty/control/quote values that Windows PowerShell 5.1 cannot pass reliably as native argv. #>
    param([string] $Value)
    if ([string]::IsNullOrWhiteSpace($Value) -or $Value -match '[\x00-\x1f\x7f"]') {
        throw 'Unsupported native argument.'
    }
}

function Find-LitePython {
    <# Prefer the installed Python launcher; fall back after a missing, broken or old interpreter. #>
    $versionCheck = 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'
    foreach ($candidate in @('py.exe', 'python.exe')) {
        try {
            $found = Get-Command $candidate -ErrorAction Stop
            # Name supports test functions; Source is the absolute installed executable path.
            $executable = if ($found.Source) { $found.Source } else { $found.Name }
            $prefix = @()
            if ($candidate -eq 'py.exe') { $prefix = @('-3') }
            # The fixed -c check neither consumes stdin nor executes user-supplied text.
            $global:LASTEXITCODE = $null
            & $executable @prefix -X utf8 -c $versionCheck 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) {
                return [pscustomobject] @{ Executable = $executable; Prefix = $prefix; IsNative = ($found.CommandType -eq 'Application') }
            }
        } catch {
            # Missing commands and launcher errors both permit the next existing candidate.
            continue
        }
    }
    return $null
}

function ConvertTo-LiteLegacyArgument {
    <# PS5.1 quotes spaced argv but does not escape a trailing backslash before its closing quote. #>
    param([string] $Value)
    # Embedded quotes were rejected earlier; only the closing-quote edge case needs repair.
    if ($Value -match '\s' -and $Value -match '(\\+)$') {
        return $Value + $Matches[1]
    }
    return $Value
}

# Keep failures actionable but fixed: do not print keys, arbitrary args or exception messages.
$failureHint = 'Invalid lite option. Use a Windows absolute -DataDir, a valid environment-variable name, and supported flags.'
try {
    # powershell.exe -File can report ExpectingInput for redirected EOF without any PS records.
    $hasPipelineInput = $hasPipelineInput -and $pipelineInput.Count -gt 0
    # Piped text belongs only to chat; other modes must not silently discard private input.
    if ($hasPipelineInput -and $Command -ne 'chat') { throw 'Pipeline input requires chat.' }
    $nativeArgs = @($Command.ToLowerInvariant())
    foreach ($option in @(
        @('DataDir', '--data-dir'),
        @('ModelBase', '--model-base'),
        @('Model', '--model'),
        @('ApiKeyEnv', '--api-key-env'),
        @('PersonaFile', '--persona-file')
    )) {
        if ($PSBoundParameters.ContainsKey($option[0])) {
            $value = [string] $PSBoundParameters[$option[0]]
            Assert-LiteArgument $value
            # Drive-relative C:state and Linux /state paths are not Windows absolute paths.
            if ($option[0] -eq 'DataDir' -and $value -notmatch '^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+(?:[\\/]|$))') {
                throw 'DataDir must be a Windows absolute path.'
            }
            # Only a variable name is forwarded; the secret remains in the inherited environment.
            if ($option[0] -eq 'ApiKeyEnv' -and $value -cnotmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
                throw 'ApiKeyEnv must be an environment-variable name.'
            }
            $nativeArgs += @($option[1], $value)
        }
    }
    if ($PSBoundParameters.ContainsKey('HistoryTurns')) { $nativeArgs += @('--history-turns', [string] $HistoryTurns) }
    if ($PSBoundParameters.ContainsKey('Port')) { $nativeArgs += @('--port', [string] $Port) }
    if ($NoBrowser) { $nativeArgs += '--no-browser' }
    if ($NonInteractive) { $nativeArgs += '--non-interactive' }

    $failureHint = 'Python 3.10+ for Windows was not found. Install Python 3.10 or newer manually from https://www.python.org/downloads/windows/ (enable the Python launcher or Add Python to PATH), open a new terminal, then retry. Nothing was downloaded or installed.'
    $python = Find-LitePython
    if ($null -eq $python) { throw 'No supported Python interpreter.' }
    $failureHint = 'Could not launch the native lite runtime. Check that tools/lite_native.py exists in this checkout.'
    # Resolve from this script, never from the caller cwd; spaces and Unicode remain single argv entries.
    $nativeScript = Join-Path $PSScriptRoot 'lite_native.py'
    if (-not (Test-Path -LiteralPath $nativeScript -PathType Leaf)) { throw 'Native runtime is missing.' }
    $launchArgs = @($python.Prefix) + @('-X', 'utf8', '-B', $nativeScript) + $nativeArgs
    # Newer PowerShell uses correct native quoting; mocks are functions, not native argv.
    $legacyQuoting = ($PSVersionTable.PSVersion -lt [version] '7.3') -or ($PSNativeCommandArgumentPassing -eq 'Legacy')
    if ($python.IsNative -and $legacyQuoting) {
        $launchArgs = @($launchArgs | ForEach-Object { ConvertTo-LiteLegacyArgument $_ })
    }
    $global:LASTEXITCODE = $null
    if ($hasPipelineInput) {
        # PS5.1 native stdin also consults Console.InputEncoding, even for a script pipeline.
        # Restore console state afterwards; no environment or persistent user setting is changed.
        $savedOutputEncoding = $global:OutputEncoding
        $savedInputEncoding = [Console]::InputEncoding
        try {
            # Native pipeline marshalling consults the global value in Windows PowerShell 5.1.
            $global:OutputEncoding = New-Object System.Text.UTF8Encoding($false)
            [Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
            $pipelineInput | & $python.Executable @launchArgs
        } finally {
            $global:OutputEncoding = $savedOutputEncoding
            [Console]::InputEncoding = $savedInputEncoding
        }
    } else {
        # No pipeline, capture or stdin reads: preserve interactive console and redirected native stdin.
        & $python.Executable @launchArgs
    }
    if ($null -eq $LASTEXITCODE) { throw 'Python did not return an exit status.' }
    exit $LASTEXITCODE
} catch {
    [Console]::Error.WriteLine('Windows lite launcher: ' + $failureHint)
    exit 2
}
} # end: launch exactly once after optional chat pipeline collection
