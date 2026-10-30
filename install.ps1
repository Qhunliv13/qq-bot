# Single Windows quick-launch entry. Parameter validation lives only in tools/windows.ps1.
# Keep this script parameterless: splatting automatic $args preserves named flags and arrays.
# Parameter-binding errors must terminate rather than exit with a stale/null native status.
$ErrorActionPreference = 'Stop'
try {
    $launcher = Join-Path $PSScriptRoot 'tools\windows.ps1'
    # Forward pipeline input explicitly without replacing the interactive console otherwise.
    if ($MyInvocation.ExpectingInput) { $input | & $launcher @args }
    else { & $launcher @args }
    exit $LASTEXITCODE
} catch {
    # Fixed text avoids exposing arbitrary rejected argument values or credentials.
    [Console]::Error.WriteLine('Windows launcher: invalid arguments or launch failure; check docs/WINDOWS.md.')
    exit 2
}
