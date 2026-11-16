<#
.SYNOPSIS
    Suite backend : dependances verrouillees, lint, contrats hors-ligne, pytest.

.DESCRIPTION
    Rejoue localement le job `test` de .github/workflows/ci-api.yml (memes
    `--ignore`, meme `-m`, meme DEBUG=true) plus les deux controles hors-ligne
    de ci-data-contracts.yml qui ne demandent ni base ni reseau.

    Aucun secret n'est lu. Toutes les commandes sont relatives a ce depot.

.PARAMETER Paths
    Chemins ou identifiants pytest a jouer a la place de `tests/`. Relatifs a
    api/backend (ex: tests/test_source_catalog.py).

.PARAMETER SkipLint
    Saute ruff check et ruff format --check. Les controles de contrat
    hors-ligne et pytest sont joues quand meme.

.EXAMPLE
    pwsh -NoProfile -File scripts/test-backend.ps1
.EXAMPLE
    pwsh -NoProfile -File scripts/test-backend.ps1 -SkipLint -Paths tests/test_source_catalog.py
#>

[CmdletBinding()]
param(
    [string]$RunManifest = $env:DC_TEST_RUN_MANIFEST,
    [string[]]$Paths,
    [switch]$SkipLint
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
    $BackendDir = Join-Path $RepoRoot 'api/backend'

    if (-not (Test-Path -LiteralPath $BackendDir)) {
        throw "Repertoire backend introuvable : $BackendDir"
    }

    # --deselect motives du plan 01-03. Chaque entree porte l'identifiant du test
    # ET sa reference dans docs/KNOWN-ISSUES.md ; un test ecarte sans reference
    # n'a pas sa place ici.
    # Etat a la cloture du plan 01-03 (gate D-02) : VIDE. La suite backend complete
    # passe sans qu'aucun test soit ecarte — 4272 passed, 41 skipped (tous anterieurs
    # a `transfer-3394395a`), 16 deselected par les `-m` de ci-api.yml.
    $Explained = @()

    function Invoke-Step {
        param(
            [Parameter(Mandatory = $true)][string]$Label,
            [Parameter(Mandatory = $true)][string[]]$Arguments
        )
        Write-Host ''
        Write-Host "==> $Label"
        Write-Host "    uv $($Arguments -join ' ')"
        & uv @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$Label a echoue (exit $LASTEXITCODE)"
        }
    }

    Push-Location $BackendDir
    try {
        Invoke-Step -Label 'Dependances verrouillees' -Arguments @('sync', '--locked', '--group', 'dev')

        if (-not $SkipLint) {
            Invoke-Step -Label 'ruff check' -Arguments @('run', 'ruff', 'check', 'app/', 'tests/', 'scripts/')
            Invoke-Step -Label 'ruff format --check' -Arguments @('run', 'ruff', 'format', '--check', 'app/', 'tests/', 'scripts/')
            Invoke-Step -Label 'mypy (cliquet)' -Arguments @('run', 'python', 'scripts/mypy_ratchet.py')
        }
        else {
            Write-Host ''
            Write-Host '==> Lint saute (-SkipLint)'
        }

        Invoke-Step -Label 'Contrat semantique (hors-ligne)' `
            -Arguments @('run', 'python', '-m', 'scripts.sync_semantic_contract', '--check')

        Invoke-Step -Label 'Audit des artefacts golden (hors-ligne)' -Arguments @(
            'run', 'python', 'scripts/verify_golden_cases.py',
            '--audit-artifacts-only',
            '--manifest', '../../contracts/golden-cases/v1/manifest.json',
            '--frontend-fixture', '../../frontend/src/lib/data/__fixtures__/golden-cases.ts'
        )

        # Commande exacte du job `test` de ci-api.yml. `tests/` est remplace par
        # -Paths quand l'appelant en fournit ; les --ignore et le -m ne bougent pas.
        $targets = if ($Paths -and $Paths.Count -gt 0) { $Paths } else { @('tests/') }

        $pytestArgs = @('run', 'pytest') + $targets + @(
            '-v', '--tb=short',
            '--ignore=tests/test_integration_v6.py',
            '--ignore=tests/test_all_datasets.py',
            '--ignore=tests/test_foncier_pm.py',
            '--ignore=tests/data_quality/test_dq_proxy_live.py',
            '--ignore=tests/integration',
            '-m', 'not live'
        )
        foreach ($entry in $Explained) {
            $pytestArgs += @('--deselect', $entry.Id)
        }

        $previousDebug = $env:DEBUG
        $env:DEBUG = 'true'
        try {
            Invoke-Step -Label 'pytest backend' -Arguments $pytestArgs
        }
        finally {
            if ($null -eq $previousDebug) {
                Remove-Item Env:\DEBUG -ErrorAction SilentlyContinue
            }
            else {
                $env:DEBUG = $previousDebug
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
Write-Host 'BACKEND ok'

}
exit $RunnerExit
