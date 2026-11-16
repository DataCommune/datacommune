<#
.SYNOPSIS
    Enchaine les quatre suites puis arrete les ressources, et rend un rapport.

.DESCRIPTION
    Ordre : backend, PostGIS, frontend (-Gate all), e2e, puis
    scripts/stop-resources.ps1.

    Par defaut, une suite en echec n'interrompt pas les suivantes : le but est
    de rendre un etat complet en un seul passage. Le code de retour est 1 des
    qu'une suite echoue. -FailFast arrete a la premiere.

    Derniere ligne : `RESULT backend=<PASS|FAIL> postgis=<...> frontend=<...> e2e=<...>`

.PARAMETER Matrix
    Passe -Matrix a scripts/test-e2e.ps1 (matrice cross-moteur).

.PARAMETER FailFast
    Arrete a la premiere suite en echec ; les suivantes sont marquees SKIP.

.EXAMPLE
    pwsh -NoProfile -File scripts/test-all.ps1
.EXAMPLE
    pwsh -NoProfile -File scripts/test-all.ps1 -Matrix
#>

[CmdletBinding()]
param(
    [string]$RunManifest = $env:DC_TEST_RUN_MANIFEST,
    [switch]$Matrix,
    [switch]$FailFast
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'test-resources.ps1')
$InvocationRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$OwnTestRun = -not $RunManifest
$SavedRunEnvironment = Save-TestEnvironment @('DC_TEST_RUN_MANIFEST','DC_TEST_RUN_ID',
    'DC_TEST_SCOPE_ID','E2E_APP_ORIGIN','E2E_SOURCE_ORIGIN','VITE_API_URL',
    'PUBLIC_DC_API_KEY','DEV_API_PROXY_TARGET')
$RunInitialized = $false
$ResourceScope = ''
$global:LASTEXITCODE = 0
$RunnerExit = 1
$results = [ordered]@{
    backend  = 'SKIP'
    postgis  = 'SKIP'
    frontend = 'SKIP'
    e2e      = 'SKIP'
}
$failed = $false

try {
    $RunManifest = Initialize-TestRun -RepoRoot $InvocationRoot -RunManifest $RunManifest
    $RunInitialized = $true
    Set-TestRunEnvironment $RunManifest
    $ResourceScope = [guid]::NewGuid().ToString('N')
    $env:DC_TEST_SCOPE_ID = $ResourceScope


    # No host-wide cleanup script is called by this runner.

    function Invoke-Suite {
        param(
            [Parameter(Mandatory = $true)][string]$Key,
            [Parameter(Mandatory = $true)][string]$Script,
            [string[]]$Arguments = @()
        )
        if ($FailFast -and $script:failed) {
            Write-Host ''
            Write-Host "==> $Key saute (-FailFast apres un echec)"
            return
        }
        $path = Join-Path $PSScriptRoot $Script
        Write-Host ''
        Write-Host "===================== $Key ====================="
        & pwsh -NoProfile -File $path -RunManifest $RunManifest @Arguments
        if ($LASTEXITCODE -eq 0) {
            $script:results[$Key] = 'PASS'
        }
        else {
            $script:results[$Key] = 'FAIL'
            $script:failed = $true
        }
    }

        Invoke-Suite -Key 'backend'  -Script 'test-backend.ps1'
        Invoke-Suite -Key 'postgis'  -Script 'test-postgis.ps1'
        Invoke-Suite -Key 'frontend' -Script 'test-frontend.ps1' -Arguments @('-Gate', 'all')
        if ($Matrix) {
            Invoke-Suite -Key 'e2e' -Script 'test-e2e.ps1' -Arguments @('-Matrix')
        }
        else {
            Invoke-Suite -Key 'e2e' -Script 'test-e2e.ps1'
        }

    $RunnerExit = [int]$failed

}
catch {
    Write-Error $_ -ErrorAction Continue
    $RunnerExit = if ($LASTEXITCODE) { $LASTEXITCODE } else { 1 }
}
finally {
    $PriorRunnerExit = $RunnerExit
    $RunCleanupFailed = $false
    try {
        if ($RunInitialized -and $OwnTestRun) { Stop-TestRun -RunManifest $RunManifest }
        elseif ($RunInitialized) { Stop-TestRun -RunManifest $RunManifest -ScopeId $ResourceScope }
    }
    catch {
        $RunCleanupFailed = $true
        Write-Error $_ -ErrorAction Continue
    }
    finally { Restore-TestEnvironment $SavedRunEnvironment }
    if ($RunCleanupFailed) {
        if (-not $PriorRunnerExit) { $RunnerExit = 1 }
    }
}

Write-Host ''
Write-Host "RESULT backend=$($results['backend']) postgis=$($results['postgis']) frontend=$($results['frontend']) e2e=$($results['e2e']) cleanup=$(if ($RunCleanupFailed) { 'FAIL' } else { 'PASS' })"

exit $RunnerExit
