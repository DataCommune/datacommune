<# Stop only one explicitly owned invocation. No discovery by port or prefix. #>
[CmdletBinding()]
param([string]$RunManifest = $env:DC_TEST_RUN_MANIFEST)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'test-resources.ps1')
try {
    if (-not $RunManifest) { throw 'RunManifest is required; host-wide cleanup is forbidden' }
    Stop-TestRun -RunManifest $RunManifest
    Write-Host 'RESOURCES stopped (owned invocation only)'
    exit 0
}
catch {
    Write-Error $_ -ErrorAction Continue
    exit 1
}
