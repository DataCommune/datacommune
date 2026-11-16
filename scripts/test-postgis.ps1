<#
.SYNOPSIS
    Suite PostGIS : tests d'integration via le harnais jetable (T-43-01).

.DESCRIPTION
    Passe par api/backend/scripts/run_contract_integration.ps1, seul point
    d'entree supporte : il provisionne un conteneur PostGIS jetable (tmpfs,
    port ephemere sur 127.0.0.1), joue les chemins pytest demandes, puis
    detruit le conteneur dans un `finally`.

    Sans -Paths, rejoue les appels de ci-data-contracts.yml : la liste du job
    `postgis-integration` DANS L'ORDRE DU WORKFLOW, puis le job
    `recovery-image` (test_l11_recovery_image.py, avec une image construite
    depuis api/docker/Dockerfile), puis les deux identifiants de contrat golden.
    Un appel = une base jetable distincte. La liste est LUE dans le workflow,
    jamais recopiee ici : une divergence workflow <-> disque leve une erreur au
    lieu d'executer silencieusement un sous-ensemble.

    Dans tous les cas, le nettoyage de cette invocation est effectue en `finally`.

.PARAMETER Paths
    Chemins ou identifiants pytest relatifs a api/backend. Un seul appel au
    harnais est fait, avec ces chemins.

.EXAMPLE
    pwsh -NoProfile -File scripts/test-postgis.ps1
.EXAMPLE
    pwsh -NoProfile -File scripts/test-postgis.ps1 -Paths tests/integration/test_postgis_harness.py
#>

[CmdletBinding()]
param(
    [string]$RunManifest = $env:DC_TEST_RUN_MANIFEST,
    [string[]]$Paths
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
    $BackendDir = Join-Path $RepoRoot 'api/backend'
    $Harness = Join-Path $BackendDir 'scripts/run_contract_integration.ps1'
    # No host-wide cleanup script is called by this runner.

    if (-not (Test-Path -LiteralPath $Harness)) {
        throw "Harnais introuvable : $Harness"
    }

    # Chaque element est un appel distinct au harnais (donc une base jetable
    # distincte), comme dans ci-data-contracts.yml.
    # Un lot = @{ Paths = <string[]>; Env = <hashtable ou $null> }.
    $batches = @()
    if ($Paths -and $Paths.Count -gt 0) {
        $batches += @{ Paths = @($Paths); Env = $null }
    }
    else {
        # La liste ET SON ORDRE viennent du workflow, pas d'un tri alphabetique.
        # L'ordre n'est pas cosmetique : les fichiers d'un meme appel partagent une
        # seule base jetable. Mesure du 2026-09-20 (plan 01-03) : en ordre
        # alphabetique, test_rnb_liens_elargissement.py joue apres des tests qui ont
        # deja cree `etl_publications`, et ses 7 tests tombent en erreur de setup
        # (`alembic upgrade head` -> DuplicateTable). En ordre du workflow il joue en
        # 4e position, sur une base encore propre — comme en CI.
        # Un groupe = un bloc `run:` du workflow = un appel au harnais, donc une
        # base distincte : test_l11_recovery_image.py a son propre job en CI
        # (`recovery-image`) et doit garder sa propre base ici.
        $workflowPath = Join-Path $RepoRoot '.github/workflows/ci-data-contracts.yml'
        if (-not (Test-Path -LiteralPath $workflowPath)) {
            throw "Workflow introuvable : $workflowPath"
        }

        $groups = @()
        $current = @()
        foreach ($line in Get-Content -LiteralPath $workflowPath) {
            if ($line -match '^\s*-\s') {
                # Nouvelle etape du workflow : on ferme le groupe en cours.
                if ($current.Count -gt 0) { $groups += , $current; $current = @() }
            }
            $found = [regex]::Match($line, 'tests/integration/\S+\.py')
            if ($found.Success) { $current += $found.Value }
        }
        if ($current.Count -gt 0) { $groups += , $current }

        $listed = @($groups | ForEach-Object { $_ } | Sort-Object -Unique)
        $integrationDir = Join-Path $BackendDir 'tests/integration'
        $onDisk = @(
            Get-ChildItem -LiteralPath $integrationDir -Filter 'test_*.py' -File |
                ForEach-Object { "tests/integration/$($_.Name)" } |
                Sort-Object -Unique
        )
        if ($onDisk.Count -eq 0) {
            throw "Aucun test d'integration trouve sous $integrationDir"
        }
        # Executions locales opt-in (LOCAL_OPT_IN de
        # tests/test_integration_collection_contract.py) : sautees sans leur
        # variable, donc jamais listees par le workflow ; jouees avec -Paths.
        $localOptIn = @('tests/integration/test_bdnb_zips_locaux.py')
        $onDisk = @($onDisk | Where-Object { $localOptIn -notcontains $_ })
        # Garde de non-vacuite : un fichier present sur le disque et absent du
        # workflow ne serait joue par personne — exactement le defaut que
        # tests/test_integration_collection_contract.py surveille cote pytest.
        $missing = @(Compare-Object -ReferenceObject $onDisk -DifferenceObject $listed |
                Where-Object { $_.SideIndicator -ne '==' } |
                ForEach-Object { "$($_.SideIndicator) $($_.InputObject)" })
        if ($missing.Count -gt 0) {
            throw ("Divergence workflow <-> disque pour tests/integration : " +
                ($missing -join ' ; '))
        }

        foreach ($group in $groups) {
            $batches += @{ Paths = $group; Env = $null }
        }
        $batches += @{
            Paths = @(
                'tests/test_golden_cases_contract.py::test_documentary_source_sql_executes_with_dates_and_numerics',
                'tests/test_golden_cases_contract.py::test_lifecycle_source_sql_executes_independently_on_091_facts'
            )
            Env   = $null
        }
    }

    # test_l11_recovery_image.py exige une image construite depuis api/docker/Dockerfile
    # (job `recovery-image` de ci-data-contracts.yml) et un repertoire de preuves hors
    # du checkout. Les deux sont fournis ici, comme le job CI les fournit ; le test
    # n'est pas ecarte.
    $recoveryEvidenceDir = $null
    $recoveryBatch = @($batches | Where-Object { $_.Paths -match 'test_l11_recovery_image\.py' })
    if ($recoveryBatch.Count -gt 0) {
        $dockerfile = Join-Path $RepoRoot 'api/docker/Dockerfile'
        $iidFile = Join-Path ([System.IO.Path]::GetTempPath()) ("dc-l11-" + [guid]::NewGuid().ToString('N').Substring(0, 10) + '.iid')
        Write-Host ''
        Write-Host '==> Construction de l''image de recuperation (api/docker/Dockerfile)'
        & docker build --iidfile $iidFile -f $dockerfile (Join-Path $RepoRoot 'api/backend')
        if ($LASTEXITCODE -ne 0) {
            throw "docker build a echoue (exit $LASTEXITCODE)"
        }
        $imageId = (& docker image inspect --format '{{.Id}}' (Get-Content -LiteralPath $iidFile -Raw).Trim()).Trim()
        Remove-Item -LiteralPath $iidFile -ErrorAction SilentlyContinue
        if ($imageId -notmatch '^sha256:[0-9a-f]{64}$') {
            throw "Identifiant d'image inattendu : $imageId"
        }
        # mkdir(exist_ok=False) cote test : le repertoire ne doit pas exister.
        $recoveryEvidenceDir = Join-Path ([System.IO.Path]::GetTempPath()) ("dc-l11-recovery-" + [guid]::NewGuid().ToString('N').Substring(0, 10))
        foreach ($batch in $recoveryBatch) {
            $batch.Env = @{
                L11_RECOVERY_IMAGE        = $imageId
                L11_RECOVERY_EVIDENCE_DIR = $recoveryEvidenceDir
            }
        }
        Write-Host "    image $imageId"
        Write-Host "    preuves $recoveryEvidenceDir"
    }

    $exitCode = 0
        $index = 0
        foreach ($batch in $batches) {
            $index++
            $targets = $batch.Paths
            Write-Host ''
            Write-Host "==> Harnais PostGIS jetable, appel $index/$($batches.Count) ($($targets.Count) cible(s))"
            $restore = @{}
            if ($batch.Env) {
                foreach ($key in $batch.Env.Keys) {
                    $restore[$key] = [System.Environment]::GetEnvironmentVariable($key)
                    Set-Item -Path "Env:$key" -Value $batch.Env[$key]
                }
            }
            try {
                & pwsh -NoProfile -File $Harness -RunManifest $RunManifest @targets
            }
            finally {
                foreach ($key in $restore.Keys) {
                    if ($null -eq $restore[$key]) {
                        Remove-Item -Path "Env:$key" -ErrorAction SilentlyContinue
                    }
                    else {
                        Set-Item -Path "Env:$key" -Value $restore[$key]
                    }
                }
            }
            if ($LASTEXITCODE -ne 0) {
                $exitCode = $LASTEXITCODE
                Write-Warning "Appel $index au harnais a rendu $LASTEXITCODE"
                break
            }
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
    Write-Host 'POSTGIS ok'
}
exit $RunnerExit
