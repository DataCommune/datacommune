<#
.SYNOPSIS
    Rejeu headless de la recette de la phase 3 sur donnees reelles (plan 03-13, D-53).

.DESCRIPTION
    Joue frontend/tests/recette/*.recette.ts (configuration
    frontend/playwright.recette.config.ts) contre l'API de la base de travail,
    ouverte sur le reseau local par `workbase-recette.sh up` (script prive).
    Hors gate hermetique : ce rejeu n'est ni dans e2e-manifest.json ni dans
    test-all.ps1.

    Prerequis (README, Procedure locale) :
      - SHA de la phase deploye (`workbase-deploy.sh <sha>`, script prive) ;
      - cle publique de recette dans $env:PUBLIC_DC_API_KEY, tiree sur le
        poste et enregistree par `workbase-anon-key.sh` (script prive) ; sa
        valeur n'est jamais imprimee ;
      - fenetre ouverte (`workbase-recette.sh up`), refermee par
        `workbase-recette.sh down` apres le rejeu, quel que soit le resultat.

    Runner Playwright headless et contexte isole uniquement (AGENTS.md).
    le nettoyage de cette invocation est effectue en `finally`.
    Sortie : `RECETTE ok`, sinon code de retour non nul.

.PARAMETER ApiTarget
    Origine de l'API (http://<ipv4>:8000). Par defaut : WORKBASE_IP du
    reglage local de l'hyperviseur (ignore par git), port 8000.

.PARAMETER Grep
    Filtre Playwright facultatif (ex. `Tracer`).

.EXAMPLE
    pwsh -NoProfile -File scripts/test-recette.ps1
#>

[CmdletBinding()]
param(
    [string]$RunManifest = $env:DC_TEST_RUN_MANIFEST,
    [string]$ApiTarget,
    [string]$Grep
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
    $RunManifest = Initialize-TestRun -RepoRoot $InvocationRoot -RunManifest $RunManifest
    $RunInitialized = $true
    Set-TestRunEnvironment $RunManifest
    $ResourceScope = [guid]::NewGuid().ToString('N')
    $env:DC_TEST_SCOPE_ID = $ResourceScope


    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
    $RunWeb = Set-TestRunWeb -RunManifest $RunManifest
    $FrontendDir = Join-Path $RepoRoot 'frontend'
    # No host-wide cleanup script is called by this runner.

    if (-not $ApiTarget) {
        $localEnv = Join-Path $RepoRoot 'infra/pve02/pve02.local.env'
        $line = if (Test-Path -LiteralPath $localEnv) {
            Get-Content -LiteralPath $localEnv |
                Where-Object { $_ -match '^WORKBASE_IP=' } | Select-Object -First 1
        }
        if (-not $line) { throw 'WORKBASE_IP absent de infra/pve02/pve02.local.env (gabarit : pve02.local.env.example)' }
        $ApiTarget = "http://$($line.Substring('WORKBASE_IP='.Length).Trim()):8000"
    }
    if ($ApiTarget -notmatch '^http://[\d.]+:\d+$') {
        throw "ApiTarget doit etre http://<ipv4>:<port> : $ApiTarget"
    }
    $key = [string]$env:PUBLIC_DC_API_KEY
    if ($key -notmatch '^dc_anon_[A-Za-z0-9_-]{43,128}$') {
        # Jamais la valeur : seulement le constat.
        throw 'PUBLIC_DC_API_KEY doit contenir la cle dc_anon_ de la seance (voir README, Procedure locale)'
    }

    $env:DEV_API_PROXY_TARGET = $ApiTarget
    $arguments = @('playwright', 'test', '--config', 'playwright.recette.config.ts', '--reporter=line')
    if ($Grep) { $arguments += @('--grep', $Grep) }

    $exitCode = 0
        Push-Location $FrontendDir
        try {
            Write-Host "==> Rejeu sur donnees reelles, API $ApiTarget"
            & npx @arguments
            $exitCode = $LASTEXITCODE
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
    Write-Host 'RECETTE ok'
}
exit $RunnerExit
