<#
.SYNOPSIS
    Suite frontend hors navigateur : gates node, type-check, vitest, build.

.DESCRIPTION
    Rejoue les etapes sans navigateur du job `ci` de
    .github/workflows/ci-frontend.yml, dans le meme ordre. Les specs Playwright
    sont dans scripts/test-e2e.ps1.

    PUBLIC_DC_API_KEY vaut `dc_anon_e2e_fixture` : cle anonyme publique par
    construction, la meme que celle de playwright.config.ts. Aucun secret n'est
    lu ni ecrit.

.PARAMETER Gate
    all    : gates, check, unit, build (dans cet ordre) — defaut
    gates  : security:gates, qa:gates, qa:design, test:e2e:manifest, test:e2e:no-sleeps
    check  : npm run check
    unit   : npm run test (vitest)
    build  : npm run build, npm run test:prerender puis npm run test:served

.EXAMPLE
    pwsh -NoProfile -File scripts/test-frontend.ps1 -Gate unit
.EXAMPLE
    pwsh -NoProfile -File scripts/test-frontend.ps1 -Gate all
#>

[CmdletBinding()]
param(
    [string]$RunManifest = $env:DC_TEST_RUN_MANIFEST,
    [ValidateSet('all', 'gates', 'check', 'unit', 'build')]
    [string]$Gate = 'all'
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
try {
    if ($RunManifest) {
        $RunManifest = Initialize-TestRun -RepoRoot $InvocationRoot -RunManifest $RunManifest
        $RunInitialized = $true
        Set-TestRunEnvironment $RunManifest
    }
    $ResourceScope = [guid]::NewGuid().ToString('N')
    $env:DC_TEST_SCOPE_ID = $ResourceScope


    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
    $FrontendDir = Join-Path $RepoRoot 'frontend'

    if (-not (Test-Path -LiteralPath (Join-Path $FrontendDir 'package.json'))) {
        throw "frontend/package.json introuvable sous $RepoRoot"
    }

    $env:VITE_API_URL = 'http://127.0.0.1:8000'
    $env:PUBLIC_DC_API_KEY = 'dc_anon_e2e_fixture'

    $scripts = switch ($Gate) {
        'gates' { @('security:gates', 'qa:gates', 'qa:design', 'test:e2e:manifest', 'test:e2e:no-sleeps') }
        'check' { @('check') }
        'unit'  { @('test') }
        'build' { @('build', 'test:prerender', 'test:served') }
        'all' {
            @('security:gates', 'qa:gates', 'qa:design', 'test:e2e:manifest', 'test:e2e:no-sleeps') +
            @('check') + @('test') + @('build', 'test:prerender', 'test:served')
        }
    }

    Push-Location $RepoRoot
    try {
        foreach ($name in $scripts) {
            Write-Host ''
            Write-Host "==> npm --prefix frontend run $name"
            & npm --prefix frontend run $name
            if ($LASTEXITCODE -ne 0) {
                throw "npm run $name a echoue (exit $LASTEXITCODE)"
            }
        }
    }
    finally {
        Pop-Location
    }

    $RunnerExit = 0

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

if ($RunnerExit -eq 0) {
Write-Host ''
Write-Host "FRONTEND ok (gate=$Gate)"

}
exit $RunnerExit
