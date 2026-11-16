<#
.SYNOPSIS
    Specs Playwright, runner headless et contexte isole.

.DESCRIPTION
    Rejoue la gate Chromium de .github/workflows/ci-frontend.yml. Sans -Specs,
    selectionne les specs classees `critical` dans frontend/e2e-manifest.json,
    exactement comme le `node -e` de la CI.

    Contrainte AGENTS.md, non negociable : runner Playwright headless et
    contexte isole uniquement — jamais de navigateur personnel, jamais de mode
    fenetre, jamais d'interface graphique, jamais de CDP sur une session
    existante. Ce script ne passe aucune option d'affichage a Playwright ; le
    defaut de playwright.config.ts est headless.

    Les serveurs (vite dev et fixture DPE sur deux ports alloues a cette execution) sont demarres et
    arretes par Playwright lui-meme (`reuseExistingServer: false`) ;
    le manifeste de cette invocation permet de nettoyer un runner
    interrompu en laisserait un orphelin.

.PARAMETER Specs
    Chemins de specs relatifs a frontend/ (ex: tests/e2e/smoke.spec.ts).

.PARAMETER Projects
    Moteur(s) de la passe des specs (defaut : chromium). `firefox` ou `webkit` ne jouent que les
    specs que leur testMatch accepte (playwright.config.ts) : pour rejouer une spec de la carte sur
    un seul moteur sans la matrice entiere (plan 04.10).

.PARAMETER Matrix
    Ajoute la matrice qualite cross-moteur (Firefox + WebKit) apres la passe
    Chromium, comme la CI. Installe les deux moteurs au prealable.

.EXAMPLE
    pwsh -NoProfile -File scripts/test-e2e.ps1 -Specs tests/e2e/smoke.spec.ts
.EXAMPLE
    pwsh -NoProfile -File scripts/test-e2e.ps1 -Matrix
#>

[CmdletBinding()]
param(
    [string]$RunManifest = $env:DC_TEST_RUN_MANIFEST,
    [string[]]$Specs,
    [ValidateSet('chromium', 'firefox', 'webkit')]
    [string[]]$Projects = @('chromium'),
    [switch]$Matrix
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'test-resources.ps1')
$InvocationRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$OwnTestRun = -not $RunManifest
$SavedRunEnvironment = Save-TestEnvironment @('DC_TEST_RUN_MANIFEST','DC_TEST_RUN_ID',
    'DC_TEST_SCOPE_ID','E2E_APP_ORIGIN','E2E_SOURCE_ORIGIN','VITE_API_URL',
    'PUBLIC_DC_API_KEY','DEV_API_PROXY_TARGET','DC_TEST_STAGE')
$RunInitialized = $false
$ResourceScope = ''
$global:LASTEXITCODE = 0
$RunnerExit = 1
try {
    $RunManifest = Initialize-TestRun -RepoRoot $InvocationRoot -RunManifest $RunManifest
    $RunInitialized = $true
    Set-TestRunEnvironment $RunManifest
    $ResourceScope = [guid]::NewGuid().ToString('N')
    $env:DC_TEST_SCOPE_ID = $ResourceScope


    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
    $RunWeb = Set-TestRunWeb -RunManifest $RunManifest
    $FrontendDir = Join-Path $RepoRoot 'frontend'
    $ManifestPath = Join-Path $FrontendDir 'e2e-manifest.json'
    # No host-wide cleanup script is called by this runner.

    if (-not (Test-Path -LiteralPath $ManifestPath)) {
        throw "Manifeste introuvable : $ManifestPath"
    }

    $env:VITE_API_URL = 'http://127.0.0.1:8000'
    $env:PUBLIC_DC_API_KEY = 'dc_anon_e2e_fixture'

    # Selection des specs : meme logique que la CI, lecture de e2e-manifest.json.
    $selected = @()
    if ($Specs -and $Specs.Count -gt 0) {
        $selected = @($Specs)
    }
    else {
        $manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
        $selected = @(
            $manifest.specs.PSObject.Properties |
                Where-Object { $_.Value.classification -eq 'critical' } |
                ForEach-Object { "tests/e2e/$($_.Name)" } |
                Sort-Object
        )
        if ($selected.Count -eq 0) {
            throw 'Aucune spec classee critical dans e2e-manifest.json'
        }
    }

    Write-Host "==> Specs $($Projects -join '+') ($($selected.Count)) :"
    $selected | ForEach-Object { Write-Host "    $_" }

    $exitCode = 0
    Push-Location $FrontendDir
        try {
            Write-Host ''
            $projectArgs = @($Projects | ForEach-Object { "--project=$_" })
            Write-Host "==> npx playwright test <specs> $($projectArgs -join ' ') --reporter=line"
            $env:DC_TEST_STAGE = if ($Projects -contains 'chromium') { 'chromium' } else { 'matrix' }
            & npx playwright test @selected @projectArgs --reporter=line
            $exitCode = $LASTEXITCODE

            if ($exitCode -eq 0 -and $Matrix) {
                Write-Host ''
                Write-Host '==> npx playwright install firefox webkit'
                & npx playwright install firefox webkit
                if ($LASTEXITCODE -ne 0) {
                    throw "Installation des moteurs firefox/webkit echouee (exit $LASTEXITCODE)"
                }
                Write-Host ''
                Write-Host '==> Matrice qualite cross-moteur (firefox + webkit)'
                $env:DC_TEST_STAGE = 'matrix'
                # With -Specs, the named specs too: the testMatch of the firefox and webkit projects keeps only
                # those allowed on three engines (D-01, widened to the parcel map by plan 04.10-10).
                $matrixSpecs = @('tests/e2e/matrix-qualite.spec.ts')
                if ($Specs -and $Specs.Count -gt 0) { $matrixSpecs += @($Specs) }
                & npx playwright test @matrixSpecs --project=firefox --project=webkit --reporter=line
                $exitCode = $LASTEXITCODE
            }
        }
        finally {
            Pop-Location
        }

    $RunnerExit = $exitCode

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
    Write-Host 'E2E ok'
}
exit $RunnerExit
