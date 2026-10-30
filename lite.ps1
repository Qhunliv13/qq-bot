# Thin native Windows entry; parameter binding belongs only to tools/windows-lite.ps1.
# Keeping automatic $args intact preserves switches, integer values and pipeline text.
$ErrorActionPreference = 'Stop'
try {
    $launcher = Join-Path $PSScriptRoot 'tools\windows-lite.ps1'
    # A script pipeline is not inherited automatically; leave terminal stdin alone otherwise.
    if ($MyInvocation.ExpectingInput) { $input | & $launcher @args }
    else { & $launcher @args }
    exit $LASTEXITCODE
} catch {
    # Fixed text never exposes rejected arguments, API keys or private chat content.
    [Console]::Error.WriteLine('Windows lite launcher: invalid arguments or launch failure; check the supported lite options.')
    exit 2
}
