# Windows-only entry point. All installation and Docker logic remains in tools/bootstrap.py inside WSL.
[CmdletBinding()]
param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet('init', 'doctor', 'up', 'status', 'logs', 'health', 'chat')]
    [string] $Command,
    [string] $Distro,
    [string] $ModelBase,
    [string] $Model,
    [string] $ApiKeyEnv,
    [string] $PersonaFile,
    [string] $DataDir,
    [string] $RuntimeName,
    [switch] $NonInteractive,
    [string[]] $OwnerQQ
)

# No shell-command construction: PowerShell passes each entry as a distinct native argument.
# Fail before WSL is launched on values that could create ambiguity or injection.
function Assert-SafeArgument {
    <# Reject quote/control characters and shell metacharacters, which have no supported use in these flags. #>
    param([string] $Value, [string] $Label)
    if ([string]::IsNullOrWhiteSpace($Value) -or $Value -match '[\x00-\x1f\x7f"''`$;&|<>]') {
        throw "Invalid ${Label}: empty, quoted or shell-special values are not supported."
    }
}

# WSL receives console or redirected stdin directly, with no chat text in argv.
try {
    $wslCommand = Get-Command 'wsl.exe' -ErrorAction Stop
    # A function named wsl.exe can stand in for the executable in offline tests.
    $wsl = if ($wslCommand.Source) { $wslCommand.Source } else { $wslCommand.Name }
    # The distro is a WSL identifier, not an executable, command line or free-form path.
    $distroArgs = @()
    if ($PSBoundParameters.ContainsKey('Distro')) {
        Assert-SafeArgument $Distro 'Distro'
        if ($Distro -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]*$') { throw 'Invalid Distro name.' }
        $distroArgs = @('--distribution', $Distro)
    }

    $repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).ProviderPath
    # wslpath executes without bash -c; it translates the exact checkout containing this launcher.
    $linuxRepoResult = @(& $wsl @distroArgs --exec wslpath -a -u $repo)
    if ($LASTEXITCODE -ne 0 -or $linuxRepoResult.Count -ne 1) { throw 'WSL could not translate the repository path.' }
    $linuxRepo = [string] $linuxRepoResult[0]
    if ($linuxRepo -notmatch '^/' -or $linuxRepo -match '[\x00-\x1f\x7f"''`$;&|<>]') {
        throw 'WSL returned an unsafe repository path.'
    }

    $bootstrapArgs = @($Command)
    if ($PSBoundParameters.ContainsKey('ModelBase')) {
        Assert-SafeArgument $ModelBase 'ModelBase'
        $bootstrapArgs += @('--model-base', $ModelBase)
    }
    if ($PSBoundParameters.ContainsKey('Model')) {
        Assert-SafeArgument $Model 'Model'
        $bootstrapArgs += @('--model', $Model)
    }
    if ($PSBoundParameters.ContainsKey('ApiKeyEnv')) {
        Assert-SafeArgument $ApiKeyEnv 'ApiKeyEnv'
        if ($ApiKeyEnv -cnotmatch '^[A-Za-z_][A-Za-z0-9_]*$') { throw 'ApiKeyEnv must be a Linux environment-variable name.' }
        $bootstrapArgs += @('--api-key-env', $ApiKeyEnv)
    }
    if ($PSBoundParameters.ContainsKey('PersonaFile')) {
        Assert-SafeArgument $PersonaFile 'PersonaFile'
        if (-not $PersonaFile.StartsWith('/')) { throw 'PersonaFile must be a Linux absolute path visible inside WSL.' }
        $bootstrapArgs += @('--persona-file', $PersonaFile)
    }
    if ($PSBoundParameters.ContainsKey('DataDir')) {
        Assert-SafeArgument $DataDir 'DataDir'
        if (-not $DataDir.StartsWith('/')) { throw 'DataDir must be a Linux absolute path outside the repository.' }
        $bootstrapArgs += @('--data-dir', $DataDir)
    }
    if ($PSBoundParameters.ContainsKey('RuntimeName')) {
        Assert-SafeArgument $RuntimeName 'RuntimeName'
        $bootstrapArgs += @('--runtime-name', $RuntimeName)
    }
    if ($NonInteractive) { $bootstrapArgs += '--non-interactive' }
    foreach ($owner in $OwnerQQ) {
        if ($owner -cnotmatch '^[0-9]{5,20}$') { throw 'OwnerQQ must be 5-20 ASCII digits.' }
        $bootstrapArgs += @('--owner-qq', $owner)
    }

    if ($ApiKeyEnv -and $Command -in @('init', 'up')) {
        [Console]::Error.WriteLine('Set the API key in the selected WSL Linux distribution environment before running this command; a Windows environment variable is not automatically forwarded.')
    }
    $launchArgs = @($distroArgs) + @('--cd', $linuxRepo, '--exec', 'python3', 'tools/bootstrap.py') + $bootstrapArgs
    # WSL inherits the console/redirected standard input directly; no prompt is put in argv.
    & $wsl @launchArgs
    if ($null -eq $LASTEXITCODE) { throw 'WSL did not return an exit code.' }
    exit $LASTEXITCODE
} catch {
    # Do not include arbitrary exception details, native command lines or secret values.
    [Console]::Error.WriteLine('Windows launcher: invalid option or WSL invocation failed; check paths, selected distribution and Linux prerequisites.')
    exit 2
}
