<# Local native proof: two owned fixture servers, exact identities, no host-wide cleanup. #>
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
. (Join-Path $RepoRoot 'scripts/test-resources.ps1')
$savedEnvironment = Save-TestEnvironment @('E2E_APP_ORIGIN','E2E_SOURCE_ORIGIN','DC_TEST_SCOPE_ID')
$shell = (Get-Command pwsh -CommandType Application | Select-Object -First 1).Source
$launcher = Join-Path $RepoRoot 'scripts/start-test-web.ps1'
$proof = [ordered]@{
    head = (& git -C $RepoRoot rev-parse HEAD)
    started_utc = [datetime]::UtcNow.ToString('O')
    result = 'unverified'
    runs = @()
    observations = @()
    cleanup = @()
}
$launchIdentities = @()
$manifests = @()
$exitCode = 1

function Assert-OwnedIdentity($Identity) {
    $native = Get-CimInstance Win32_Process -Filter "ProcessId=$($Identity.pid)" -ErrorAction Stop
    if (-not $native -or $native.CreationDate.ToUniversalTime().Ticks.ToString() -ne $Identity.creation_date -or
        $native.CommandLine -ne $Identity.command_line) { throw 'Owned process identity changed' }
}

function Wait-RegisteredHealthy([string]$Manifest, [string]$Label) {
    $deadline = [datetime]::UtcNow.AddSeconds(25)
    $lastFailure = ''
    while ([datetime]::UtcNow -lt $deadline) {
        $run = Get-TestRun $Manifest
        $nodes = @($run.processes | Where-Object { $_.status -eq 'active' -and $_.command_line.Contains('dpe-source-server.ts') -and $_.command_line -notmatch 'start-test-web\.ps1' })
        if ($nodes.Count -eq 1) {
            Assert-OwnedIdentity $nodes[0]
            try {
                $response = Invoke-WebRequest -Uri "http://127.0.0.1:$($run.web.source_port)/health" -TimeoutSec 2
                if ($response.StatusCode -eq 200) {
                    $proof.observations += [ordered]@{ step = "$Label-ready"; http = 200; identity = $nodes[0] }
                    return $nodes[0]
                }
            }
            catch { $lastFailure = $_.Exception.Message }
        }
        Start-Sleep -Milliseconds 100
    }
    throw "Owned fixture $Label failed readiness: $lastFailure"
}

try {
    foreach ($label in @('A','B')) {
        $manifest = Initialize-TestRun -RepoRoot $RepoRoot
        $manifests += $manifest
        $web = Set-TestRunWeb -RunManifest $manifest
        if ($web.app_port -in @(5173,4174) -or $web.source_port -in @(5173,4174)) { throw 'Legacy port selected' }
        $run = Get-TestRun $manifest
        $directory = Split-Path -Parent $manifest
        $arguments = @('-NoProfile','-File', ('"' + $launcher + '"'), '-RunManifest', ('"' + $manifest + '"'), '-Kind','source')
        $process = Start-Process -FilePath $shell -ArgumentList $arguments -WorkingDirectory $RepoRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $directory 'launcher.out.log') -RedirectStandardError (Join-Path $directory 'launcher.err.log')
        $native = Get-CimInstance Win32_Process -Filter "ProcessId=$($process.Id)" -ErrorAction Stop
        if (-not $native -or $native.ParentProcessId -ne $PID -or -not $native.CommandLine.Contains($launcher)) { throw 'Launcher identity unavailable' }
        $identity = [pscustomobject]@{pid=$process.Id;creation_date=$native.CreationDate.ToUniversalTime().Ticks.ToString();command_line=$native.CommandLine}
        $launchIdentities += $identity
        Register-TestProcess -RunManifest $manifest -ProcessId $process.Id -CommandLine $native.CommandLine -ScopeId 'live-proof'
        $proof.runs += [ordered]@{ label=$label;manifest=$manifest;run_id=$run.run_id;web=$web;launcher=$identity }
        $node = Wait-RegisteredHealthy $manifest $label
        if ($label -eq 'A') { $nodeA = $node }
    }
    $runA = Get-TestRun $manifests[0]
    $runB = Get-TestRun $manifests[1]
    if ($runA.web.source_port -eq $runB.web.source_port) { throw 'Two live fixtures share a port' }
    Stop-TestRun -RunManifest $manifests[1]
    Assert-OwnedIdentity $nodeA
    Assert-OwnedIdentity $proof.runs[0].launcher
    $response = Invoke-WebRequest -Uri "http://127.0.0.1:$($runA.web.source_port)/health" -TimeoutSec 3
    if ($response.StatusCode -ne 200 -or (Get-TestRun $manifests[0]).status -ne 'active') { throw 'Run A was disturbed by cleanup of B' }
    $proof.observations += [ordered]@{ step='A-after-B-cleanup';http=200;identity=$nodeA }
    $proof.result = 'pass'
    $exitCode = 0
}
catch {
    $proof.result = 'fail'
    $proof.observations += [ordered]@{step='failure';message=$_.Exception.Message}
    Write-Error $_ -ErrorAction Continue
}
finally {
    foreach ($manifest in $manifests) {
        try {
            Stop-TestRun -RunManifest $manifest
            $run = Get-TestRun $manifest
            foreach ($identity in $run.processes) {
                if (Get-CimInstance Win32_Process -Filter "ProcessId=$($identity.pid)" -ErrorAction Stop) { throw 'Owned PID survives final cleanup' }
            }
            if ($run.status -ne 'stopped') { throw 'Invocation cleanup not complete' }
            $proof.cleanup += [ordered]@{manifest=$manifest;result='pass';owned_processes=$run.processes.Count}
            Copy-Item -LiteralPath $manifest -Destination (Join-Path $PSScriptRoot ("native-web-$($run.run_id)-final.json"))
        }
        catch {
            $exitCode = 1
            $proof.result = 'unverified'
            $proof.cleanup += [ordered]@{manifest=$manifest;result='unverified';message=$_.Exception.Message}
            Write-Error $_ -ErrorAction Continue
        }
    }
    # A registration failure still leaves a launch identity observed by its parent.
    foreach ($identity in $launchIdentities) {
        try { Stop-OwnedTestProcess -Identity $identity }
        catch { $exitCode=1;$proof.result='unverified';Write-Error $_ -ErrorAction Continue }
    }
    Restore-TestEnvironment $savedEnvironment
    $proof.finished_utc = [datetime]::UtcNow.ToString('O')
    $proof.files = @('scripts/test-resources.ps1','scripts/start-test-web.ps1','reports/reliability-lot0/verify-live-web.ps1') |
        ForEach-Object { [ordered]@{path=$_;sha256=(Get-FileHash -LiteralPath (Join-Path $RepoRoot $_) -Algorithm SHA256).Hash.ToLowerInvariant()} }
    $proof | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'native-web-proof.json') -Encoding utf8
}
Write-Host "NATIVE_WEB result=$($proof.result) cleanup=$($proof.cleanup.Count) exit=$exitCode"
exit $exitCode
