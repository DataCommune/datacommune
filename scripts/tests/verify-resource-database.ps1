# Exercises the versioned ownership helper and PostGIS harness with two owned runs.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
. (Join-Path $repo 'scripts/test-resources.ps1')
$compose = Join-Path $repo 'api/docker-compose.contract-tests.yml'
$harness = Join-Path $repo 'api/backend/scripts/run_contract_integration.ps1'
$manifestA = Initialize-TestRun -RepoRoot $repo
$manifestB = Initialize-TestRun -RepoRoot $repo
$runA = Get-TestRun $manifestA
$projectA = "dc-verify-$($runA.run_id)-$([guid]::NewGuid().ToString('N').Substring(0, 10))"
Register-TestProject -RunManifest $manifestA -ProjectName $projectA -ComposeFile $compose
$evidence = [ordered]@{
    manifest_a = $manifestA; manifest_b = $manifestB; project_a = $projectA
    inputs = @{}; observations = @(); cleanup = 'not_run'; passed = $false
}
foreach ($relative in @('scripts/test-resources.ps1', 'api/backend/scripts/run_contract_integration.ps1',
    'api/docker-compose.contract-tests.yml', 'api/backend/tests/integration/test_postgis_harness.py')) {
    $evidence.inputs[$relative] = (Get-FileHash -LiteralPath (Join-Path $repo $relative) -Algorithm SHA256).Hash
}
$containerA = $null
function Assert-FirstRunAlive([string]$Stage) {
    $ids = @(& docker compose -f $compose -p $projectA ps -a -q)
    if ($LASTEXITCODE -ne 0 -or $ids.Count -ne 1 -or $ids[0] -ne $containerA) {
        throw "First run identity changed at $Stage"
    }
    $running = & docker inspect --format '{{.State.Running}}' $containerA
    if ($LASTEXITCODE -ne 0 -or $running -ne 'true') { throw "First run stopped at $Stage" }
    & docker compose -f $compose -p $projectA exec -T db pg_isready -U contract_test
    if ($LASTEXITCODE -ne 0) { throw "First database unavailable at $Stage" }
    $evidence.observations += @{ stage = $Stage; first_run_alive = $true; container = $containerA }
}
function Assert-SecondRunProjectsStopped {
    $second = Get-TestRun $manifestB
    foreach ($project in $second.projects) {
        if ($project.status -ne 'stopped') { throw 'Second run project not marked stopped' }
        $ids = @(& docker compose -f $project.compose_file -p $project.name ps -a -q)
        if ($LASTEXITCODE -ne 0 -or $ids.Count) { throw 'Second run project still exists' }
    }
}
$failure = $null
try {
    & docker compose -f $compose -p $projectA up -d --wait --wait-timeout 60
    if ($LASTEXITCODE -ne 0) { throw 'First owned project startup failed' }
    $ids = @(& docker compose -f $compose -p $projectA ps -a -q)
    if ($LASTEXITCODE -ne 0 -or $ids.Count -ne 1) { throw 'First owned container unavailable' }
    $containerA = $ids[0]
    Assert-FirstRunAlive 'before-second-run'

    & pwsh -NoProfile -File $harness -RunManifest $manifestB 'tests/integration/test_postgis_harness.py'
    $positiveExit = $LASTEXITCODE
    if ($positiveExit -ne 0) { throw "Positive second run failed: $positiveExit" }
    Assert-SecondRunProjectsStopped
    Assert-FirstRunAlive 'after-second-run-success'

    # Deliberately missing input proves cleanup after a test command fails.
    & pwsh -NoProfile -File $harness -RunManifest $manifestB 'tests/integration/__expected_missing_owned_probe__.py'
    $negativeExit = $LASTEXITCODE
    if ($negativeExit -ne 4) { throw "Expected pytest usage error 4, got $negativeExit" }
    Assert-SecondRunProjectsStopped
    Assert-FirstRunAlive 'after-second-run-failure'
    Stop-TestRun -RunManifest $manifestB
    Assert-FirstRunAlive 'after-second-run-final-cleanup'
    $evidence.positive_exit = $positiveExit
    $evidence.expected_negative_exit = $negativeExit
    $evidence.passed = $true
}
catch {
    $failure = $_.Exception.Message
    Write-Error $_ -ErrorAction Continue
}
finally {
    $cleanupFailures = @()
    foreach ($manifest in @($manifestB, $manifestA)) {
        try { Stop-TestRun -RunManifest $manifest }
        catch { $cleanupFailures += $_.Exception.Message }
    }
    $evidence.cleanup = if ($cleanupFailures.Count) { 'failed' } else { 'passed' }
    $evidence.cleanup_failures = $cleanupFailures
    $evidence.failure = $failure
    if ($cleanupFailures.Count) { $evidence.passed = $false }
    $evidence | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'live-database.json') -Encoding utf8
}
if (-not $evidence.passed) { exit 1 }
Write-Host 'ISOLATION_DATABASE verified with two owned runs'
exit 0
